# -*- coding: utf-8 -*-
# Hardware-Equalizer for Dreambox DM920 (uses the BAPE equalizer via dm920eq.ko)
import os

from Plugins.Plugin import PluginDescriptor
from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from Components.ActionMap import ActionMap
from Components.ConfigList import ConfigListScreen
from Components.config import config, configfile, ConfigSubsection, ConfigYesNo, ConfigSelection, getConfigListEntry
from Components.Sources.StaticText import StaticText
from enigma import eTimer

import eqmath

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
MODULE = os.path.join(PLUGIN_DIR, "dm920eq.ko")
PROC = "/proc/dm920eq"

GAIN_CHOICES = [(str(v), "%+d dB" % v if v else "0 dB") for v in range(eqmath.GAIN_MIN, eqmath.GAIN_MAX + 1)]
LEVEL_CHOICES = [(str(v), "%d dB" % v if v else "0 dB") for v in range(0, -13, -1)]

config.plugins.hardwareeq = ConfigSubsection()
config.plugins.hardwareeq.agc = ConfigSelection(default="0", choices=[
	("0", "aus"), ("1", "leicht (bis -6 dB)"), ("2", "mittel (bis -10 dB)"), ("3", "stark (bis -15 dB)")])
config.plugins.hardwareeq.radio_hdmi = ConfigYesNo(default=True)


class Profile(object):
	"""One EQ setting (enabled, preset, level, protect, 8 bands). "tv" keeps the original config
	names (band0 ...), "hdmi" uses the prefix hdmi_. The module loads the matching set by itself."""

	def __init__(self, key, prefix, name, proc):
		cfg = config.plugins.hardwareeq
		self.key, self.name, self.proc = key, name, proc
		setattr(cfg, prefix + "enabled", ConfigYesNo(default=True))
		setattr(cfg, prefix + "preset", ConfigSelection(default="flat", choices=[(k, n) for k, n, g in eqmath.PRESETS]))
		setattr(cfg, prefix + "level", ConfigSelection(default="0", choices=LEVEL_CHOICES))
		setattr(cfg, prefix + "protect", ConfigYesNo(default=False))
		self.enabled = getattr(cfg, prefix + "enabled")
		self.preset = getattr(cfg, prefix + "preset")
		self.level = getattr(cfg, prefix + "level")
		self.protect = getattr(cfg, prefix + "protect")
		self.bands = []
		for i in range(len(eqmath.BANDS)):
			setattr(cfg, "%sband%d" % (prefix, i), ConfigSelection(default="0", choices=GAIN_CHOICES))
			self.bands.append(getattr(cfg, "%sband%d" % (prefix, i)))

	def items(self):
		return [self.enabled, self.preset, self.level, self.protect] + self.bands

	def gains(self):
		return [int(b.value) for b in self.bands]

	def set_preset(self, key):
		for k, name, gains in eqmath.PRESETS:
			if k == key:
				self.preset.value = k
				if gains is not None:
					for b, g in zip(self.bands, gains):
						b.value = str(g)
				return True
		return False

	def match_preset(self):
		"""after band edits: show a preset name again if the curve matches one"""
		self.preset.value = "custom"
		for k, n, g in eqmath.PRESETS:
			if g is not None and list(g) == self.gains():
				self.preset.value = k
				return

	def copy_from(self, other):
		for a, b in zip(self.items(), other.items()):
			a.value = b.value


PROFILES = [Profile("tv", "", "Fernsehen", "biquads"), Profile("hdmi", "hdmi_", "HDMI-Eingang", "biquads_hdmi")]
PROFILE = dict((p.key, p) for p in PROFILES)
if PROFILE["hdmi"].preset.saved_value is None:      # first start with two profiles: HDMI starts as a copy
	PROFILE["hdmi"].copy_from(PROFILE["tv"])
BANDS_CFG = PROFILE["tv"].bands                      # compatibility


def log(msg):
	print "[HardwareEQ]", msg


def module_loaded():
	return os.path.isdir(PROC)


def load_module():
	if module_loaded():
		return True
	if not os.path.exists(MODULE):
		log("module missing: %s" % MODULE)
		return False
	ret = os.system("insmod %s" % MODULE)
	log("insmod ret=%d" % ret)
	return module_loaded()


def unload_module():
	if module_loaded():
		os.system("rmmod dm920eq")


def write_proc(name, data):
	try:
		f = open(os.path.join(PROC, name), "w")
		f.write(data + "\n")
		f.close()
		return True
	except Exception, e:
		log("write %s failed: %s" % (name, e))
		return False


def read_status():
	try:
		return open(os.path.join(PROC, "status")).read()
	except Exception:
		return ""


def read_agc():
	vals = {}
	try:
		for line in open(os.path.join(PROC, "agc")):
			parts = line.split()
			if len(parts) == 2:
				vals[parts[0]] = int(parts[1])
	except Exception:
		pass
	return vals


def all_items():
	cfg = config.plugins.hardwareeq
	items = [cfg.agc, cfg.radio_hdmi]
	for p in PROFILES:
		items += p.items()
	return items


def save_settings():
	for item in all_items():
		item.save()
	config.plugins.hardwareeq.save()
	configfile.save()


def cancel_settings():
	for item in all_items():
		item.cancel()


def active_profile():
	"""which filter set the module has loaded right now ("tv" or "hdmi")"""
	for line in read_status().splitlines():
		if line.startswith("eq-set"):
			return "hdmi" if line.split()[1] == "hdmi" else "tv"
	return "tv"


# ---- HDMI input (via the Newnigma2 "HDMI" plugin) ----

def _nn2_hdmi():
	import sys
	for name, m in sys.modules.items():
		if m is not None and name.endswith("newnigma2.hdmi.plugin") and hasattr(m, "HDMID"):
			return m
	try:
		from Plugins.newnigma2.hdmi import plugin as m
		return m
	except Exception, e:
		log("NN2 HDMI plugin not available: %s" % e)
		return None


def _hdmi_screen(session):
	cands = [getattr(session, "current_dialog", None)]
	for x in getattr(session, "dialog_stack", []):
		cands.append(x[0] if isinstance(x, tuple) else x)
	for d in cands:
		if d is not None and d.__class__.__name__ == "HdmiMain":
			return d
	return None


def hdmi_available():
	return _nn2_hdmi() is not None


def hdmi_active(session):
	return _hdmi_screen(session) is not None


def open_hdmi(session):
	"""Start the HDMI input like Blue -> HDMI -> Play does. Returns True if it is (now) active."""
	if _hdmi_screen(session) is not None:
		return True
	m = _nn2_hdmi()
	if m is None:
		return False
	try:
		m.HDMID(session)
		return True
	except Exception, e:
		log("opening HDMI input failed: %s" % e)
		return False


def close_hdmi(session):
	d = _hdmi_screen(session)
	if d is None:
		return True
	if d is not getattr(session, "current_dialog", None):
		return False            # something is open on top of it, leave it alone
	try:
		d.leave()
		return True
	except Exception, e:
		log("closing HDMI input failed: %s" % e)
		return False


def apply_settings():
	"""Push both EQ profiles and the level control into the hardware.
	Returns {"tv": (preamp_db, max_boost_db) or None, "hdmi": ...}; None if the module is missing."""
	cfg = config.plugins.hardwareeq
	agc = cfg.agc.value
	if not any(p.enabled.value for p in PROFILES) and agc == "0":
		if module_loaded():
			for p in PROFILES:
				write_proc(p.proc, eqmath.flat_line())
			write_proc("agc", "0")
		return None
	if not load_module():
		return None
	write_proc("agc", agc)
	res = {}
	for p in PROFILES:
		if not p.enabled.value:
			write_proc(p.proc, eqmath.flat_line())
			res[p.key] = None
			continue
		biquads, preamp = eqmath.design(p.gains(), int(p.level.value), p.protect.value)
		res[p.key] = (preamp, eqmath.max_gain_db(biquads)) if write_proc(p.proc, eqmath.to_line(biquads)) else None
	return res


class HardwareEQSetup(ConfigListScreen, Screen):
	skin = """
	<screen name="HardwareEQSetup" position="center,center" size="1100,900" title="Hardware-Equalizer">
		<widget name="config" position="30,20" size="1040,630" itemHeight="45" scrollbarMode="showOnDemand" />
		<eLabel position="30,665" size="1040,2" backgroundColor="#00777777" />
		<widget source="info" render="Label" position="30,675" size="1040,120" font="Regular;28" foregroundColor="#00cccccc" />
		<eLabel position="30,840" size="15,40" backgroundColor="#00e01e1e" />
		<widget source="key_red" render="Label" position="55,835" size="230,50" font="Regular;28" valign="center" />
		<eLabel position="295,840" size="15,40" backgroundColor="#0016a016" />
		<widget source="key_green" render="Label" position="320,835" size="230,50" font="Regular;28" valign="center" />
		<eLabel position="560,840" size="15,40" backgroundColor="#00e8c81e" />
		<widget source="key_yellow" render="Label" position="585,835" size="230,50" font="Regular;28" valign="center" />
		<eLabel position="825,840" size="15,40" backgroundColor="#001e5ae0" />
		<widget source="key_blue" render="Label" position="850,835" size="230,50" font="Regular;28" valign="center" />
	</screen>"""

	def __init__(self, session):
		Screen.__init__(self, session)
		self.setTitle("Hardware-Equalizer & Werbe-Bremse")
		self["key_red"] = StaticText("Abbrechen")
		self["key_green"] = StaticText("Speichern")
		self["key_yellow"] = StaticText("Neutral")
		self["key_blue"] = StaticText("EQ an/aus")
		self["info"] = StaticText("")
		self._busy = False
		# which profile is edited: starts with the source that plays right now
		self.which = ConfigSelection(default=active_profile() if hdmi_available() else "tv",
			choices=[(p.key, p.name) for p in PROFILES])
		ConfigListScreen.__init__(self, [], session=session, on_change=self.changed)
		self["actions"] = ActionMap(["SetupActions", "ColorActions"], {
			"cancel": self.cancel,
			"red": self.cancel,
			"green": self.save,
			"save": self.save,
			"yellow": self.reset,
			"blue": self.toggle,
		}, -2)
		self.eq_info = ""
		self.timer = eTimer()
		try:
			self.timer_conn = self.timer.timeout.connect(self.tick)
		except AttributeError:
			self.timer.callback.append(self.tick)
		self.build()
		self.onLayoutFinish.append(self.update)
		self.onClose.append(self.timer.stop)

	def prof(self):
		return PROFILE[self.which.value]

	def build(self):
		cfg = config.plugins.hardwareeq
		p = self.prof()
		lst = [getConfigListEntry("Werbe-Bremse (nur Fernsehen)", cfg.agc)]
		if hdmi_available():
			lst.append(getConfigListEntry("Equalizer einstellen für", self.which))
		lst += [
			getConfigListEntry("Equalizer aktiv", p.enabled),
			getConfigListEntry("Voreinstellung", p.preset),
			getConfigListEntry("Gesamtpegel (zusätzlich leiser)", p.level),
			getConfigListEntry("Übersteuerungsschutz", p.protect),
		]
		for label, band in zip(eqmath.BAND_LABELS, p.bands):
			lst.append(getConfigListEntry("Band %s" % label, band))
		if hdmi_available():
			lst.append(getConfigListEntry("Radio-Taste schaltet auf HDMI-Eingang", cfg.radio_hdmi))
		self["config"].list = lst
		self["config"].l.setList(lst)

	def changed(self):
		if self._busy:
			return
		self._busy = True
		p = self.prof()
		cur = self["config"].getCurrent()
		if cur and cur[1] is self.which:
			self.build()
		elif cur and cur[1] is p.preset:
			p.set_preset(p.preset.value)
			self["config"].l.setList(self["config"].list)
		elif cur and cur[1] in p.bands:
			p.match_preset()
			self["config"].l.setList(self["config"].list)
		self._busy = False
		self.update()

	def update(self):
		res = apply_settings()
		p = self.prof()
		other = PROFILE["hdmi" if p.key == "tv" else "tv"]
		scope = (" – gilt nur, wenn %s läuft" % ("das Fernsehen" if p.key == "tv" else "der HDMI-Eingang")) if hdmi_available() else ""
		if not p.enabled.value:
			txt = "Equalizer für %s ist aus%s." % (p.name, scope)
		elif res is None or res.get(p.key) is None:
			txt = "Fehler: Treibermodul konnte nicht geladen werden."
		else:
			preamp, boost = res[p.key]
			status = read_status()
			names = {"MAI(HDMI)": "HDMI", "SPDIF": "S/PDIF", "DAC": "DAC"}
			outs = [names[l.split()[0]] for l in status.splitlines() if l.endswith(" EQ") and l.split()[0] in names]
			where = ", ".join(outs) if outs else "keinem Ausgang (Passthrough/Mehrkanal?)"
			txt = "EQ %s%s (Ausgänge: %s)." % (p.name, scope, where)
			if boost > 0.05 and not p.protect.value:
				txt += " Anhebung bis %+.1f dB" % boost
			elif preamp < -0.05:
				txt += " Vorverstärkung %.1f dB" % preamp
		if hdmi_available():
			txt += "\nGerade aktiv: %s" % PROFILE[active_profile()].name
		self.eq_info = txt
		self.tick()
		self.timer.start(1000, False)

	def tick(self):
		txt = self.eq_info
		if config.plugins.hardwareeq.agc.value != "0":
			a = read_agc()
			if a.get("hdmi_input"):
				txt += "\nWerbe-Bremse: beim HDMI-Eingang aus (nur EQ)"
			elif not a.get("reference_valid"):
				txt += "\nWerbe-Bremse: lernt den Pegel des Programms ..."
			elif a.get("gain", 0) <= -500:
				txt += "\nWerbe-Bremse: senkt gerade um %.1f dB ab" % (-a["gain"] / 1000.0)
			else:
				txt += "\nWerbe-Bremse: bereit (keine Absenkung)"
			if "loudness" in a and "reference" in a and a.get("reference_valid") and not a.get("hdmi_input"):
				txt += "  [Lautheit %.0f, Bezug %.0f LUFS]" % (a["loudness"] / 1000.0, a["reference"] / 1000.0)
		self["info"].setText(txt)

	def reset(self):
		p = self.prof()
		p.set_preset("flat")
		p.level.value = "0"
		p.protect.value = False
		self["config"].l.setList(self["config"].list)
		self.update()

	def toggle(self):
		p = self.prof()
		p.enabled.value = not p.enabled.value
		self["config"].l.setList(self["config"].list)
		self.update()

	def save(self):
		save_settings()
		self.close()

	def cancel(self):
		cancel_settings()
		apply_settings()
		self.close()


def main(session, **kwargs):
	session.open(HardwareEQSetup)


_orig_showRadio = None


def _show_radio(self):
	if config.plugins.hardwareeq.radio_hdmi.value and open_hdmi(self.session):
		return
	_orig_showRadio(self)


def autostart(reason, **kwargs):
	"""Radio button -> HDMI input. Patched before the InfoBar is created."""
	global _orig_showRadio
	if reason != 0 or _orig_showRadio is not None:
		return
	try:
		from Screens.InfoBar import InfoBar
		_orig_showRadio = InfoBar.showRadio
		InfoBar.showRadio = _show_radio
	except Exception, e:
		log("radio button hook failed: %s" % e)


def sessionstart(reason, **kwargs):
	if reason != 0:
		return
	cfg = config.plugins.hardwareeq
	if any(p.enabled.value for p in PROFILES) or cfg.agc.value != "0":
		try:
			apply_settings()
		except Exception, e:
			log("autostart failed: %s" % e)
	try:
		import webui
		webui.register(kwargs["session"])
	except Exception, e:
		log("web interface not available: %s" % e)


def Plugins(**kwargs):
	return [
		PluginDescriptor(where=PluginDescriptor.WHERE_AUTOSTART, fnc=autostart),
		PluginDescriptor(where=PluginDescriptor.WHERE_SESSIONSTART, fnc=sessionstart),
		PluginDescriptor(name="Hardware-Equalizer", description="Klangregelung und Werbe-Bremse für HDMI- und S/PDIF-Ton",
			where=[PluginDescriptor.WHERE_PLUGINMENU, PluginDescriptor.WHERE_EXTENSIONSMENU], fnc=main),
	]
