// SPDX-License-Identifier: GPL-2.0
/* dm920eq v4: BAPE hardware EQ in decoder0 -> outputs path, ad brake, measurement taps */
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/fs.h>
#include <linux/proc_fs.h>
#include <linux/kallsyms.h>
#include <linux/err.h>
#include <linux/mutex.h>
#include <linux/string.h>
#include <linux/vmalloc.h>
#include <linux/uaccess.h>
#include <linux/delay.h>
#include <linux/slab.h>
#include <linux/kthread.h>
#include <linux/bitops.h>
#include <linux/math64.h>
#include "dm920eq_tables.h"

static int gain_permille = 1000;
module_param(gain_permille, int, 0444);
static int taps = 0;
module_param(taps, int, 0444);
static int agc = 1;
module_param(agc, int, 0444);
MODULE_PARM_DESC(agc, "0 = no ad-brake tap and thread");

typedef int (*f_create3)(void *hApe, const void *s, void **out);
typedef void (*f_getdef1)(void *s);
typedef void (*f_st_getdef)(int type, void *s);
typedef int (*f_setstages)(void *eq, void **stages, unsigned n);
typedef int (*f_h_ptr)(void *h, void *p);
typedef void (*f_getptr)(void *h, void **p);
typedef void (*f_destroy)(void *h);
typedef int (*f_mix_add)(void *mix, void *conn, const void *s);
typedef int (*f_dec)(void *dec);
typedef int (*f_cap_open)(void *hApe, unsigned idx, const void *s, void **out);
typedef int (*f_cap_consume)(void *h, unsigned n);
typedef int (*f_mix_vol)(void *mix, void *conn, void *vol);
typedef void (*f_void)(void);

static struct {
	f_void b_lock, b_unlock;
	f_mix_vol mix_getvol, mix_setvol;
	f_create3 eq_create, st_create, mix_create; f_getdef1 eq_getdef, mix_getdef, mix_getdefs, cap_getdefs;
	f_st_getdef st_getdef; f_setstages eq_setstages;
	f_h_ptr eq_addinput, eq_rminput, mix_rm, mix_addout, mix_rmout, st_setsettings, cap_getbuffer;
	f_getptr eq_getconn, mix_getconn, cap_getport; f_destroy eq_destroy, st_destroy, mix_destroy, cap_close;
	f_mix_add mix_add; f_dec dec_start, dec_stop; f_cap_open cap_open; f_cap_consume cap_consume;
} F;

#define SYM(field, name) do { F.field = (void *)kallsyms_lookup_name(name); if (!F.field) { pr_err("dm920eq: missing %s\n", name); return -ENOENT; } } while (0)

static char *ctx, *dev, *dec;
static struct mutex *dec_mutex;
static void *hApe, *eq, *stage, *eqconn, *stereo, *pre, *preconn;
static void *tapmix[2], *tapcap[2], *tapport[2];
static int ports_done[5];
static DEFINE_MUTEX(cfg_lock);
static u8 stage_settings[256];
static struct proc_dir_entry *pdir;

static const int cur_off[5] = { 0x0c, 0x1c, 0x2c, 0x4c, 0x3c };
static const int mix_off[5] = { 0x10, 0x20, 0x30, 0x50, 0x40 };
static const char *pname[5] = { "DUMMY", "DUMMY2", "DAC", "MAI(HDMI)", "SPDIF" };

/*
 * Locking. The driver runs its event handlers (audio DSP watchdog recovery, sample rate changes, ...)
 * from b_event_task with its global b_lock held; the watchdog handler then takes the decoder mutexes.
 * Decoder start/stop/format changes (ioctl path) run under the decoder mutex. Both tear down and
 * rebuild mixer outputs, including our capture buffers (BAPE_OutputCapture_P_Disable frees the
 * DFIFO group without any lock). Every BAPE call we make therefore runs with b_lock and then the
 * decoder-0 mutex held - the same order the driver uses. Lock order in this module:
 *     cfg_lock -> b_lock -> dec_mutex
 */
static void bape_lock(void)   { F.b_lock(); mutex_lock(dec_mutex); }
static void bape_unlock(void) { mutex_unlock(dec_mutex); F.b_unlock(); }

static int resolve(void)
{
	struct file *f;
	SYM(eq_create, "BAPE_Equalizer_Create"); SYM(eq_getdef, "BAPE_Equalizer_GetDefaultSettings");
	SYM(st_create, "BAPE_EqualizerStage_Create"); SYM(st_getdef, "BAPE_EqualizerStage_GetDefaultSettings");
	SYM(st_setsettings, "BAPE_EqualizerStage_SetSettings");
	SYM(eq_setstages, "BAPE_Equalizer_SetStages"); SYM(eq_addinput, "BAPE_Equalizer_AddInput");
	SYM(eq_rminput, "BAPE_Equalizer_RemoveInput"); SYM(eq_getconn, "BAPE_Equalizer_GetConnector");
	SYM(eq_destroy, "BAPE_Equalizer_Destroy"); SYM(st_destroy, "BAPE_EqualizerStage_Destroy");
	SYM(mix_add, "BAPE_Mixer_AddInput"); SYM(mix_rm, "BAPE_Mixer_RemoveInput");
	SYM(mix_getdef, "BAPE_Mixer_GetDefaultAddInputSettings");
	SYM(mix_create, "BAPE_Mixer_Create"); SYM(mix_getdefs, "BAPE_Mixer_GetDefaultSettings");
	SYM(mix_getconn, "BAPE_Mixer_GetConnector"); SYM(mix_destroy, "BAPE_Mixer_Destroy");
	SYM(mix_addout, "BAPE_Mixer_AddOutput"); SYM(mix_rmout, "BAPE_Mixer_RemoveOutput");
	SYM(cap_getdefs, "BAPE_OutputCapture_GetDefaultOpenSettings"); SYM(cap_open, "BAPE_OutputCapture_Open");
	SYM(cap_close, "BAPE_OutputCapture_Close"); SYM(cap_getport, "BAPE_OutputCapture_GetOutputPort");
	SYM(cap_getbuffer, "BAPE_OutputCapture_GetBuffer"); SYM(cap_consume, "BAPE_OutputCapture_ConsumeData");
	SYM(dec_start, "bcm_audio_decoder_start"); SYM(dec_stop, "bcm_audio_decoder_stop");
	SYM(mix_getvol, "BAPE_Mixer_GetInputVolume"); SYM(mix_setvol, "BAPE_Mixer_SetInputVolume");
	SYM(b_lock, "b_lock"); SYM(b_unlock, "b_unlock");
	f = filp_open("/proc/stb/audio/audio_delay_pcm", O_RDONLY, 0);
	if (IS_ERR(f)) return PTR_ERR(f);
	ctx = *(char **)((char *)PDE_DATA(file_inode(f)) + 0x24);
	filp_close(f, NULL);
	dev = ctx + 7216; dec = ctx + 7456; dec_mutex = (struct mutex *)(ctx + 7824);
	if (*(char **)dec != ctx || *(u32 *)(dec + 0xf0) != 0 || *(void **)(ctx + 4096 + 0xc30) != *(void **)dev) {
		pr_err("dm920eq: context sanity check failed\n"); return -ENODEV;
	}
	hApe = *(void **)dev;
	stereo = *(void **)(dev + 0xfc);
	return 0;
}

static int set_gain(int g)
{
	int ret;
	if (g < 0) g = 0;
	if (g > 1000) g = 1000;   /* never boost */
	mutex_lock(&cfg_lock);
	*(u32 *)(stage_settings + 0x10) = 1;
	*(s32 *)(stage_settings + 0x14) = ((1 << 26) / 1000) * g;
	bape_lock();
	ret = stage ? F.st_setsettings(stage, stage_settings) : 0;
	bape_unlock();
	if (!ret) gain_permille = g;
	mutex_unlock(&cfg_lock);
	pr_info("dm920eq: set gain %d ret=%d\n", g, ret);
	return ret;
}

/* ---- capture taps ---- */
struct capsnap { u32 len[2]; u32 info[2][4]; u8 data[]; };
#define CAPMAX (96 * 1024)

static u32 cap_avail(int i, void **p)
{
	u8 d[96];
	if (F.cap_getbuffer(tapcap[i], d)) return 0;
	*p = *(void **)(d + 8);
	return *(u32 *)(d + 72);
}

static int cap_open_proc(struct inode *inode, struct file *file)
{
	struct capsnap *s; int i; u32 n[2]; void *p[2]; u8 d[96];
	if (!tapcap[0] || !tapcap[1]) return -ENODEV;
	s = vzalloc(sizeof(*s) + 2 * CAPMAX);
	if (!s) return -ENOMEM;
	bape_lock();
	for (i = 0; i < 2; i++) { n[i] = cap_avail(i, &p[i]); if (n[i]) F.cap_consume(tapcap[i], n[i]); }
	for (i = 0; i < 2; i++) { n[i] = cap_avail(i, &p[i]); if (n[i]) F.cap_consume(tapcap[i], n[i]); }
	bape_unlock();
	msleep(200);
	bape_lock();
	for (i = 0; i < 2; i++) {
		F.cap_getbuffer(tapcap[i], d);
		s->info[i][0] = d[0]; s->info[i][1] = *(u32 *)(d + 4); s->info[i][2] = *(u32 *)(d + 72); s->info[i][3] = *(u32 *)(d + 76);
		n[i] = cap_avail(i, &p[i]);
	}
	for (i = 0; i < 2; i++) {
		u32 m = min3(n[0], n[1], (u32)CAPMAX) & ~7u;
		if (m && p[i]) memcpy(s->data + i * CAPMAX, p[i], m);
		s->len[i] = m;
	}
	bape_unlock();
	file->private_data = s;
	return 0;
}
static ssize_t cap_read(struct file *file, char __user *buf, size_t cnt, loff_t *pos)
{
	struct capsnap *s = file->private_data;
	return simple_read_from_buffer(buf, cnt, pos, s, sizeof(*s) + 2 * CAPMAX);
}
static int cap_release(struct inode *inode, struct file *file) { vfree(file->private_data); return 0; }
static const struct file_operations cap_fops = { .owner = THIS_MODULE, .open = cap_open_proc, .read = cap_read, .release = cap_release, .llseek = default_llseek };

static ssize_t gain_write(struct file *file, const char __user *buf, size_t cnt, loff_t *pos)
{
	char tmp[16]; int g;
	if (cnt >= sizeof(tmp)) return -EINVAL;
	if (copy_from_user(tmp, buf, cnt)) return -EFAULT;
	tmp[cnt] = 0;
	if (kstrtoint(strim(tmp), 10, &g)) return -EINVAL;
	return set_gain(g) ? -EIO : cnt;
}
static ssize_t gain_read(struct file *file, char __user *buf, size_t cnt, loff_t *pos)
{
	char tmp[16]; int l = snprintf(tmp, sizeof(tmp), "%d\n", gain_permille);
	return simple_read_from_buffer(buf, cnt, pos, tmp, l);
}
/* write: "N c0 c1 ... c(5N-1)"  (b0 b1 b2 a1 a2 per biquad, Q5.26 integers) */
static ssize_t bq_write(struct file *file, const char __user *buf, size_t cnt, loff_t *pos)
{
	char *tmp, *cur, *tok; int n, i, ret; s32 c[40];
	if (cnt > 1024) return -EINVAL;
	tmp = kmalloc(cnt + 1, GFP_KERNEL); if (!tmp) return -ENOMEM;
	if (copy_from_user(tmp, buf, cnt)) { kfree(tmp); return -EFAULT; }
	tmp[cnt] = 0; cur = tmp;
	tok = strsep(&cur, " \t\n");
	if (!tok || kstrtoint(tok, 10, &n) || n < 1 || n > 8) { kfree(tmp); return -EINVAL; }
	for (i = 0; i < 5 * n; i++) {
		do tok = strsep(&cur, " \t\n"); while (tok && !*tok);
		if (!tok || kstrtos32(tok, 10, &c[i])) { kfree(tmp); return -EINVAL; }
	}
	kfree(tmp);
	mutex_lock(&cfg_lock);
	*(u32 *)(stage_settings + 0x10) = n;
	memset(stage_settings + 0x14, 0, 8 * 20);
	for (i = 0; i < 8; i++) *(s32 *)(stage_settings + 0x14 + i * 20) = 1 << 26;
	memcpy(stage_settings + 0x14, c, 5 * n * 4);
	bape_lock();
	ret = F.st_setsettings(stage, stage_settings);
	bape_unlock();
	mutex_unlock(&cfg_lock);
	pr_info("dm920eq: set %d biquads ret=%d\n", n, ret);
	return ret ? -EIO : cnt;
}
static const struct file_operations bq_fops = { .owner = THIS_MODULE, .write = bq_write, .llseek = default_llseek };

static ssize_t status_read(struct file *file, char __user *buf, size_t cnt, loff_t *pos)
{
	char tmp[512]; int l = 0, p;
	for (p = 0; p < 5; p++) {
		void *c = *(void **)(dev + cur_off[p]);
		l += snprintf(tmp + l, sizeof(tmp) - l, "%-10s %s\n", pname[p], c == eqconn ? "EQ" : c == stereo ? "stereo (bypass!)" : c ? "other" : "none");
	}
	l += snprintf(tmp + l, sizeof(tmp) - l, "routing   %s\n", *(void **)(dev + 0xfc) == eqconn ? "driver-stereo=EQ" : "driver-stereo=raw");
	return simple_read_from_buffer(buf, cnt, pos, tmp, l);
}
static void agc_force_reapply(void);

/*
 * When the driver switches to compressed passthrough (DD+/DTS to HDMI) it removes our EQ connector
 * from every output mixer; when it returns to PCM it adds it back. After that round trip the EQ chain
 * no longer processes anything (output equals the unprocessed input: neither the filters nor the
 * attenuator act) until the EQ input and outputs are re-added. relink(2) does exactly that; the
 * worker thread calls it after every passthrough -> PCM transition. Bit 1 (re-add the decoder input
 * of the pre mixer) is kept for diagnostics only.
 * Called without b_lock (decoder stop/start), with the decoder mutex taken here.
 */
static int relink(int what)
{
	u8 ms[16]; int running, p, r1 = 0, r2 = 0, r3 = 0;
	mutex_lock(dec_mutex);
	running = *(u32 *)(dec + 0xf4);
	if (running) F.dec_stop(dec);
	if (what & 2)
		for (p = 0; p < 5; p++)
			if (*(void **)(dev + cur_off[p]) == eqconn) r3 |= F.mix_rm(*(void **)(dev + mix_off[p]), eqconn);
	if (what & 1) {
		r1 = F.mix_rm(pre, stereo);
		F.mix_getdef(ms); ms[0] = 1;
		r1 |= F.mix_add(pre, stereo, ms);
	}
	if (what & 2) {
		r2 = F.eq_rminput(eq, preconn);
		r2 |= F.eq_addinput(eq, preconn);
		for (p = 0; p < 5; p++)
			if (*(void **)(dev + cur_off[p]) == eqconn) { F.mix_getdef(ms); ms[0] = 1; r3 |= F.mix_add(*(void **)(dev + mix_off[p]), eqconn, ms); }
	}
	agc_force_reapply();
	if (running) F.dec_start(dec);
	mutex_unlock(dec_mutex);
	pr_info("dm920eq: relink %d pre=%d eq=%d out=%d\n", what, r1, r2, r3);
	return r1 | r2 | r3;
}
static ssize_t status_write(struct file *file, const char __user *buf, size_t cnt, loff_t *pos)
{
	char tmp[8]; int w;
	if (cnt >= sizeof(tmp) || copy_from_user(tmp, buf, cnt)) return -EINVAL;
	tmp[cnt] = 0;
	if (kstrtoint(strim(tmp), 10, &w) || w < 1 || w > 3) return -EINVAL;
	return relink(w) ? -EIO : cnt;
}
static const struct file_operations status_fops = { .owner = THIS_MODULE, .read = status_read, .write = status_write, .llseek = default_llseek };

static const struct file_operations gain_fops = { .owner = THIS_MODULE, .read = gain_read, .write = gain_write, .llseek = default_llseek };

/* ---------------------------------------------------------------------------------------------
 * Automatic level control ("Werbe-Bremse")
 * A capture is attached as an extra output of the driver's DAC mixer, i.e. behind the EQ and the
 * attenuator. (An own mixer fed straight from the decoder would keep the decoder's PCM branch alive
 * while the driver switches to DD+/DTS passthrough; the audio DSP then crashes in a loop when it
 * returns to PCM decoding. The DAC mixer is idled by the driver itself in passthrough.)
 * A kernel thread measures short-term loudness (ITU-R BS.1770 K-weighting), removes its own
 * attenuation from the measurement, tracks a slow programme reference and attenuates the input of
 * the intermediate mixer when the audio gets clearly louder than that reference. The EQ shifts
 * measurement and reference alike, so it does not trigger the brake.
 * It only ever attenuates (gain <= 0 dB). All levels are in milli-dB (mdB).
 * ------------------------------------------------------------------------------------------- */
#define AGC_CAP_INDEX   2
#define BLOCK_FRAMES    1920            /* 40 ms at 48 kHz */
#define SILENCE_MDB     (-60000)
#define FS_OFFSET_MDB   (132450 + 691)  /* 20*log10(2^22) + 0.691 (BS.1770 offset) */
#define VOLSZ           264

static struct task_struct *agc_task;
static void *agcmix, *agccap, *agcport;   /* agcmix: the driver's DAC mixer, not ours */
static u8 vol_base[VOLSZ];
static int vol_base_ok;

static struct {
	int mode;                /* 0 off, 1 light, 2 medium, 3 strong, 4 custom */
	int margin, ratio10, maxred;  /* mdB, ratio*10, mdB (positive) */
	/* state */
	s64 kst[2][2][4];        /* [ch][stage] x1 x2 y1 y2 */
	u64 acc; int nacc;
	s64 ms_short;            /* smoothed mean square */
	int loud, ref, gain;     /* mdB */
	s64 ref_u;               /* reference in micro-dB */
	int ref_valid, init_n, warmup; s64 init_acc;
	int silent_blocks, was_running, applied_idx, want_idx;
	int meas;                /* last block as measured at the tap, before compensation (mdB) */
	u32 loops, empty, bytes, blocks, maxchunk, dbg_d0, dbg_d4, dbg_wrap;
} A = { .applied_idx = -1 };
static void agc_force_reapply(void) { A.applied_idx = -1; }

#define AGC_CHUNK (64 * 1024)
static s32 agc_buf[AGC_CHUNK / 4];   /* private copy of captured audio, processed without locks */

static const struct { int margin, ratio10, maxred; } agc_presets[4] = {
	{ 0, 10, 0 }, { 4000, 20, 6000 }, { 3000, 30, 10000 }, { 2000, 50, 15000 },
};

static int mdb_from_energy(u64 e)   /* 10*log10(e) in mdB, e > 0 */
{
	int msb = fls64(e) - 1; u32 frac; s64 l2;
	if (msb >= 8) frac = (u32)(e >> (msb - 8)) & 0xff; else frac = (u32)(e << (8 - msb)) & 0xff;
	l2 = ((s64)msb << 16) + log2_frac_q16[frac];          /* log2 in Q16 */
	return (int)((l2 * 3010300LL) >> 16) / 1000;           /* * 10*log10(2) */
}

/* computes the wanted attenuation step (0.1 dB units); the AGC thread applies it under bape_lock */
static void agc_apply_gain(int gain_mdb)
{
	int idx = (-gain_mdb + 50) / 100;
	if (idx < 0) idx = 0;
	if (idx > 300) idx = 300;
	ACCESS_ONCE(A.want_idx) = idx;
}

/* caller holds bape_lock */
static void agc_set_volume_locked(int idx)
{
	u8 v[VOLSZ]; int i; u32 g;
	if (!vol_base_ok || idx == A.applied_idx) return;
	g = gain_q16[idx];
	memcpy(v, vol_base, VOLSZ);
	for (i = 0; i < 64; i++) {
		u32 c = ((u32 *)vol_base)[i];
		if (c) ((u32 *)v)[i] = (u32)(((u64)c * g) >> 16);
	}
	if (!F.mix_setvol(pre, stereo, v)) A.applied_idx = idx;
}

static void agc_reset_reference(void)
{
	A.ref_valid = 0; A.init_n = 0; A.init_acc = 0;
}

static void agc_block(void)
{
	u64 ms = div_u64(A.acc, A.nacc);
	s64 target = 0;
	int margin = A.margin, ratio10 = A.ratio10, maxred = A.maxred;

	A.acc = 0; A.nacc = 0;
	A.meas = ms ? mdb_from_energy(ms) - FS_OFFSET_MDB : -100000;
	if (A.applied_idx > 0) {                                /* undo our own attenuation */
		u32 g = gain_q16[A.applied_idx];
		u64 inv = div64_u64(1ULL << 48, (u64)g * g);          /* 1/g^2 in Q16, <= 2^26 */
		ms = ((ms >> 12) * inv) >> 4;
	}
	A.ms_short += ((s64)ms - A.ms_short) >> 3;               /* ~ 320 ms time constant */
	A.loud = A.ms_short > 0 ? mdb_from_energy((u64)A.ms_short) - FS_OFFSET_MDB : -100000;

	if (A.loud < SILENCE_MDB) {
		if (++A.silent_blocks > 25) agc_reset_reference();  /* 1 s of silence: new programme */
	} else {
		A.silent_blocks = 0;
		if (!A.ref_valid) {
			A.init_acc += A.loud;
			if (++A.init_n >= 75) {                          /* 3 s of audio: initial reference */
				A.ref_u = div_s64(A.init_acc, A.init_n) * 1000LL;
				A.ref_valid = 1;
				A.warmup = 1500;                             /* 60 s of faster learning */
			}
		} else {
			/* slow, biased follower: +0.6 dB/min up, -1.2 dB/min down
			 * (10x faster during the first minute after a channel change) */
			s64 d = (s64)A.loud * 1000 - A.ref_u;
			int k = A.warmup > 0 ? 10 : 1;
			if (A.warmup > 0) A.warmup--;
			if (d > 0) A.ref_u += min_t(s64, d, 400 * k);   /* 0.4 mdB per 40 ms block */
			else A.ref_u -= min_t(s64, -d, 800 * k);
		}
	}
	A.ref = (int)div_s64(A.ref_u, 1000);

	if (A.mode && A.ref_valid && A.loud > SILENCE_MDB) {
		int over = A.loud - (A.ref + margin);
		if (over > 0)
			target = -(s64)div_s64((s64)over * (ratio10 - 10), ratio10);
		if (target < -maxred) target = -maxred;
	}
	/* attack: exponential (~0.25 s), release: 1.5 dB/s */
	if ((int)target < A.gain) A.gain += ((int)target - A.gain) / 6;
	else A.gain = min_t(int, (int)target, A.gain + 60);
	if (!A.mode) A.gain = 0;
	agc_apply_gain(A.gain);
}

static inline s64 kfilt(const s64 *c, s64 *s, s64 x)
{
	s64 y = (c[0] * x + c[1] * s[0] + c[2] * s[1] - c[3] * s[2] - c[4] * s[3]) >> 28;
	s[1] = s[0]; s[0] = x; s[3] = s[2]; s[2] = y;
	return y;
}

static void agc_process(const s32 *p, u32 frames)
{
	u32 i;
	for (i = 0; i < frames; i++) {
		int ch;
		for (ch = 0; ch < 2; ch++) {
			s64 x = p[2 * i + ch] >> 9;                       /* 23 bit */
			s64 y = kfilt(kw2, A.kst[ch][1], kfilt(kw1, A.kst[ch][0], x));
			A.acc += (u64)(y * y) >> 4;
		}
		if (++A.nacc >= BLOCK_FRAMES) {
			A.blocks++;
			A.acc <<= 4;
			agc_block();
		}
	}
}

static int eq_routed(void)   /* caller holds dec_mutex: is any real output fed by the EQ? */
{
	int p;
	for (p = 2; p < 5; p++)
		if (*(void **)(dev + cur_off[p]) == eqconn) return 1;
	return 0;
}

static u32 relinks;

/* worker: EQ chain repair after passthrough, and the ad brake (if its tap exists) */
static int agc_thread(void *unused)
{
	int eq_was_on;
	mutex_lock(dec_mutex); eq_was_on = eq_routed(); mutex_unlock(dec_mutex);

	while (!kthread_should_stop()) {
		u8 d[96]; u32 n = 0; int running, eq_on;

		/* everything that touches driver objects happens under the driver's own locks, so a DSP
		 * watchdog recovery or a decoder restart can never tear the capture down underneath us */
		bape_lock();
		running = *(volatile u32 *)(dec + 0xf4);
		eq_on = eq_routed();
		if (agccap && !F.cap_getbuffer(agccap, d)) {
			const s32 *p = *(const s32 **)(d + 8);
			n = *(u32 *)(d + 72) & ~7u;
			A.dbg_d0 = d[0]; A.dbg_d4 = *(u32 *)(d + 4); A.dbg_wrap = *(u32 *)(d + 76);
			A.bytes += n; if (n > A.maxchunk) A.maxchunk = n;
			if (n > AGC_CHUNK) n = AGC_CHUNK;
			if (n && p) {
				memcpy(agc_buf, p, n);
				F.cap_consume(agccap, n);
			} else n = 0;
		}
		agc_set_volume_locked(ACCESS_ONCE(A.want_idx));
		bape_unlock();

		if (eq_on && !eq_was_on) {          /* back from passthrough: repair the EQ chain */
			relink(2);
			relinks++;
		}
		eq_was_on = eq_on;

		mutex_lock(&cfg_lock);
		A.loops++;
		if (running && !A.was_running) agc_reset_reference();   /* zap / restart */
		A.was_running = running;
		if (n) agc_process(agc_buf, n / 8); else A.empty++;
		mutex_unlock(&cfg_lock);
		msleep_interruptible(30);
	}
	return 0;
}

static void agc_set_mode(int mode, int margin, int ratio10, int maxred)
{
	mutex_lock(&cfg_lock);
	if (mode >= 0 && mode < 4) {
		margin = agc_presets[mode].margin; ratio10 = agc_presets[mode].ratio10; maxred = agc_presets[mode].maxred;
	}
	A.mode = mode; A.margin = margin;
	A.ratio10 = clamp(ratio10, 10, 200);
	A.maxred = clamp(maxred, 0, 30000);
	if (!A.mode) { A.gain = 0; agc_apply_gain(0); }   /* off: back to unity right away */
	mutex_unlock(&cfg_lock);
	pr_info("dm920eq: agc mode=%d margin=%d ratio=%d/10 maxred=%d\n", A.mode, A.margin, A.ratio10, A.maxred);
}

/* write: "<mode>" (0-3) or "4 <margin_mdB> <ratio*10> <maxred_mdB>" */
static ssize_t agc_write(struct file *file, const char __user *buf, size_t cnt, loff_t *pos)
{
	char tmp[64]; int m = 0, a = 0, b = 10, c = 0, n;
	if (cnt >= sizeof(tmp)) return -EINVAL;
	if (copy_from_user(tmp, buf, cnt)) return -EFAULT;
	tmp[cnt] = 0;
	n = sscanf(tmp, "%d %d %d %d", &m, &a, &b, &c);
	if (n < 1 || m < 0 || m > 4 || (m == 4 && n != 4)) return -EINVAL;
	agc_set_mode(m, a, b, c);
	return cnt;
}
static ssize_t agc_read(struct file *file, char __user *buf, size_t cnt, loff_t *pos)
{
	char tmp[400];
	int l = snprintf(tmp, sizeof(tmp), "mode %d\nloudness %d\nreference %d\ngain %d\nreference_valid %d\nactive %d\nmeasured %d\napplied %d\n"
		"debug loops=%u empty=%u bytes=%u blocks=%u maxchunk=%u il=%u nbuf=%u wrap=%u relinks=%u\n",
		A.mode, A.loud, A.ref, A.gain, A.ref_valid, agccap != NULL, A.meas, -100 * A.applied_idx,
		A.loops, A.empty, A.bytes, A.blocks, A.maxchunk, A.dbg_d0, A.dbg_d4, A.dbg_wrap, relinks);
	return simple_read_from_buffer(buf, cnt, pos, tmp, l);
}
static const struct file_operations agc_fops = { .owner = THIS_MODULE, .read = agc_read, .write = agc_write, .llseek = default_llseek };

static void setup_agc(void)
{
	u8 cs[32]; int ret;
	if (F.mix_getvol(pre, stereo, vol_base) == 0) vol_base_ok = 1;
	pr_info("dm920eq: base input volume %08x %08x %08x %08x muted=%d\n", ((u32 *)vol_base)[0], ((u32 *)vol_base)[1],
		((u32 *)vol_base)[8], ((u32 *)vol_base)[9], vol_base[256]);
	agcmix = *(void **)(dev + mix_off[2]);                    /* DAC mixer */
	if (!agcmix) { pr_err("dm920eq: no DAC mixer, no ad-brake tap\n"); return; }
	F.cap_getdefs(cs);
	ret = F.cap_open(hApe, AGC_CAP_INDEX, cs, &agccap);
	if (ret) { pr_err("dm920eq: agc capture open ret=%d\n", ret); agccap = NULL; agcmix = NULL; return; }
	F.cap_getport(agccap, &agcport);
	ret = F.mix_addout(agcmix, agcport);
	if (ret) { pr_err("dm920eq: agc tap addout ret=%d\n", ret); F.cap_close(agccap); agccap = NULL; agcmix = NULL; return; }
	pr_info("dm920eq: agc tap ready (addout ret=%d)\n", ret);
}
static void teardown_agc(void)
{
	if (!agcmix) return;
	if (agccap) { F.mix_rmout(agcmix, agcport); F.cap_close(agccap); agccap = NULL; }
	agcmix = NULL;
	if (vol_base_ok) F.mix_setvol(pre, stereo, vol_base);
}

static void setup_taps(void)
{
	u8 mxs[256], ms[16], cs[32]; int i, ret; void *src[2] = { stereo, eqconn };
	for (i = 0; i < 2; i++) {
		F.mix_getdefs(mxs);
		ret = F.mix_create(hApe, mxs, &tapmix[i]); if (ret) { pr_err("dm920eq: tap mix %d ret=%d\n", i, ret); tapmix[i] = NULL; continue; }
		F.mix_getdef(ms); ms[0] = 1;
		ret = F.mix_add(tapmix[i], src[i], ms); if (ret) pr_err("dm920eq: tap add %d ret=%d\n", i, ret);
		F.cap_getdefs(cs);
		ret = F.cap_open(hApe, i, cs, &tapcap[i]); if (ret) { pr_err("dm920eq: cap open %d ret=%d\n", i, ret); tapcap[i] = NULL; continue; }
		F.cap_getport(tapcap[i], &tapport[i]);
		ret = F.mix_addout(tapmix[i], tapport[i]);
		pr_info("dm920eq: tap %d ready (addout ret=%d)\n", i, ret);
	}
}
static void teardown_taps(void)
{
	int i; void *src[2] = { stereo, eqconn };
	for (i = 0; i < 2; i++) {
		if (!tapmix[i]) continue;
		if (tapcap[i]) { F.mix_rmout(tapmix[i], tapport[i]); F.cap_close(tapcap[i]); tapcap[i] = NULL; }
		F.mix_rm(tapmix[i], src[i]);
		F.mix_destroy(tapmix[i]); tapmix[i] = NULL;
	}
}

static int __init eq_init(void)
{
	u8 es[16], ms[16], mxs[256];
	int ret, running, p;
	ret = resolve(); if (ret) return ret;

	F.st_getdef(5 /* custom */, stage_settings);
	*(u32 *)(stage_settings + 0x10) = 1;
	*(s32 *)(stage_settings + 0x14) = ((1 << 26) / 1000) * min(max(gain_permille, 0), 1000);
	ret = F.st_create(hApe, stage_settings, &stage); if (ret) return -EIO;
	F.eq_getdef(es);
	ret = F.eq_create(hApe, es, &eq); if (ret) goto err_st;
	ret = F.eq_setstages(eq, &stage, 1); if (ret) goto err_eq;
	F.mix_getdefs(mxs);
	ret = F.mix_create(hApe, mxs, &pre); if (ret) goto err_eq;
	F.mix_getconn(pre, &preconn);
	ret = F.eq_addinput(eq, preconn); if (ret) goto err_pre;
	F.eq_getconn(eq, &eqconn);

	/* only the decoder mutex here (as the driver does around decoder stop/start); the AGC thread is
	 * not running yet, and b_lock must not be held across bcm_audio_decoder_stop/start */
	mutex_lock(dec_mutex);
	running = *(u32 *)(dec + 0xf4);
	if (running) F.dec_stop(dec);
	F.mix_getdef(ms); ms[0] = 1;
	ret = F.mix_add(pre, stereo, ms);
	if (ret) { if (running) F.dec_start(dec); mutex_unlock(dec_mutex); F.eq_rminput(eq, preconn); goto err_pre; }
	for (p = 0; p < 5; p++) {
		void *mix = *(void **)(dev + mix_off[p]);
		if (*(void **)(dev + cur_off[p]) != stereo) continue;
		if (F.mix_rm(mix, stereo)) continue;
		F.mix_getdef(ms); ms[0] = 1;
		ret = F.mix_add(mix, eqconn, ms);
		pr_info("dm920eq: %s -> eq ret=%d\n", pname[p], ret);
		if (ret) { F.mix_add(mix, stereo, ms); continue; }
		*(void **)(dev + cur_off[p]) = eqconn; ports_done[p] = 1;
	}
	/* let the driver route the EQ output wherever it would route plain stereo */
	*(void **)(dev + 0xfc) = eqconn;
	if (taps) setup_taps();
	if (agc) setup_agc();
	if (running) F.dec_start(dec);
	mutex_unlock(dec_mutex);

	pdir = proc_mkdir("dm920eq", NULL);
	if (pdir) {
		proc_create("gain", 0644, pdir, &gain_fops);
		proc_create("biquads", 0200, pdir, &bq_fops);
		proc_create("status", 0644, pdir, &status_fops);
		proc_create("agc", 0644, pdir, &agc_fops);
		if (taps) proc_create("capture", 0444, pdir, &cap_fops);
	}
	agc_task = kthread_run(agc_thread, NULL, "dm920eq");
	if (IS_ERR(agc_task)) agc_task = NULL;
	pr_info("dm920eq: active, gain=%d/1000\n", gain_permille);
	return 0;
err_pre: F.mix_destroy(pre);
err_eq: F.eq_destroy(eq);
err_st: F.st_destroy(stage);
	return -EIO;
}

static void __exit eq_exit(void)
{
	u8 ms[16]; int running, p, ret;
	if (agc_task) kthread_stop(agc_task);
	if (pdir) {
		if (taps) remove_proc_entry("capture", pdir);
		remove_proc_entry("gain", pdir); remove_proc_entry("biquads", pdir);
		remove_proc_entry("status", pdir); remove_proc_entry("agc", pdir);
		remove_proc_entry("dm920eq", NULL);
	}
	mutex_lock(dec_mutex);
	running = *(u32 *)(dec + 0xf4);
	if (running) F.dec_stop(dec);
	if (*(void **)(dev + 0xfc) == eqconn) *(void **)(dev + 0xfc) = stereo;
	teardown_agc();
	teardown_taps();
	for (p = 0; p < 5; p++) {
		void *mix = *(void **)(dev + mix_off[p]);
		if (*(void **)(dev + cur_off[p]) != eqconn) continue;
		ret = F.mix_rm(mix, eqconn);
		F.mix_getdef(ms); ms[0] = 1;
		ret |= F.mix_add(mix, stereo, ms);
		*(void **)(dev + cur_off[p]) = stereo;
		pr_info("dm920eq: %s restored ret=%d\n", pname[p], ret);
	}
	F.mix_rm(pre, stereo);
	F.eq_rminput(eq, preconn);
	if (running) F.dec_start(dec);
	mutex_unlock(dec_mutex);
	F.eq_destroy(eq); F.st_destroy(stage); F.mix_destroy(pre);
	pr_info("dm920eq: removed\n");
}
module_init(eq_init); module_exit(eq_exit);
MODULE_LICENSE("GPL");
