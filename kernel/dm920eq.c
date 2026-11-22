// SPDX-License-Identifier: GPL-2.0
/* dm920eq v3: BAPE hardware EQ in decoder0 -> outputs path, with measurement taps */
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

static int gain_permille = 1000;
module_param(gain_permille, int, 0444);
static int taps = 0;
module_param(taps, int, 0444);

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

static struct {
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
	ret = stage ? F.st_setsettings(stage, stage_settings) : 0;
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
	for (i = 0; i < 2; i++) { n[i] = cap_avail(i, &p[i]); if (n[i]) F.cap_consume(tapcap[i], n[i]); }
	for (i = 0; i < 2; i++) { n[i] = cap_avail(i, &p[i]); if (n[i]) F.cap_consume(tapcap[i], n[i]); }
	msleep(200);
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
	ret = F.st_setsettings(stage, stage_settings);
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
static const struct file_operations status_fops = { .owner = THIS_MODULE, .read = status_read, .llseek = default_llseek };

static const struct file_operations gain_fops = { .owner = THIS_MODULE, .read = gain_read, .write = gain_write, .llseek = default_llseek };

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
	if (running) F.dec_start(dec);
	mutex_unlock(dec_mutex);

	pdir = proc_mkdir("dm920eq", NULL);
	if (pdir) {
		proc_create("gain", 0644, pdir, &gain_fops);
		proc_create("biquads", 0200, pdir, &bq_fops);
		proc_create("status", 0444, pdir, &status_fops);
		if (taps) proc_create("capture", 0444, pdir, &cap_fops);
	}
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
	if (pdir) { remove_proc_entry("capture", pdir); remove_proc_entry("gain", pdir); remove_proc_entry("biquads", pdir); remove_proc_entry("status", pdir); remove_proc_entry("dm920eq", NULL); }
	mutex_lock(dec_mutex);
	running = *(u32 *)(dec + 0xf4);
	if (running) F.dec_stop(dec);
	if (*(void **)(dev + 0xfc) == eqconn) *(void **)(dev + 0xfc) = stereo;
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
