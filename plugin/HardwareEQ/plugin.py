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
config.plugins.hardwareeq.enabled = ConfigYesNo(default=True)
config.plugins.hardwareeq.preset = ConfigSelection(default="flat", choices=[(k, n) for k, n, g in eqmath.PRESETS])
config.plugins.hardwareeq.level = ConfigSelection(default="0", choices=LEVEL_CHOICES)
config.plugins.hardwareeq.protect = ConfigYesNo(default=False)
config.plugins.hardwareeq.agc = ConfigSelection(default="0", choices=[
	("0", "aus"), ("1", "leicht (bis -6 dB)"), ("2", "mittel (bis -10 dB)"), ("3", "stark (bis -15 dB)")])
config.plugins.hardwareeq.radio_hdmi = ConfigYesNo(default=True)
BANDS_CFG = []
for _i in range(len(eqmath.BANDS)):
	setattr(config.plugins.hardwareeq, "band%d" % _i, ConfigSelection(default="0", choices=GAIN_CHOICES))
	BANDS_CFG.append(getattr(config.plugins.hardwareeq, "band%d" % _i))


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


def current_gains():
	return [int(b.value) for b in BANDS_CFG]


def set_preset(key):
	"""Select a preset; copies its gains into the bands (except "custom")."""
	cfg = config.plugins.hardwareeq
	for k, name, gains in eqmath.PRESETS:
		if k == key:
			cfg.preset.value = k
			if gains is not None:
				for b, g in zip(BANDS_CFG, gains):
					b.value = str(g)
			return True
	return False


def save_settings():
	cfg = config.plugins.hardwareeq
	for item in [cfg.enabled, cfg.preset, cfg.level, cfg.protect, cfg.agc, cfg.radio_hdmi] + BANDS_CFG:
		item.save()
	cfg.save()
	configfile.save()


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
	"""Push EQ curve and level control into the hardware.
	Returns (preamp_db, max_boost_db) or None if the EQ is off or the module is missing."""
	cfg = config.plugins.hardwareeq
	agc = cfg.agc.value
	if not cfg.enabled.value and agc == "0":
		if module_loaded():
			write_proc("biquads", eqmath.flat_line())
			write_proc("agc", "0")
		return None
	if not load_module():
		return None
	write_proc("agc", agc)
	if not cfg.enabled.value:
		write_proc("biquads", eqmath.flat_line())
		return None
	biquads, preamp = eqmath.design(current_gains(), int(cfg.level.value), cfg.protect.value)
	if write_proc("biquads", eqmath.to_line(biquads)):
		return preamp, eqmath.max_gain_db(biquads)
	return None


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

	def build(self):
		cfg = config.plugins.hardwareeq
		lst = [
			getConfigListEntry("Werbe-Bremse (Lautstärke-Automatik)", cfg.agc),
			getConfigListEntry("Equalizer aktiv", cfg.enabled),
			getConfigListEntry("Voreinstellung", cfg.preset),
			getConfigListEntry("Gesamtpegel (zusätzlich leiser)", cfg.level),
			getConfigListEntry("Übersteuerungsschutz", cfg.protect),
		]
		if hdmi_available():
			lst.append(getConfigListEntry("Radio-Taste schaltet auf HDMI-Eingang", cfg.radio_hdmi))
		for label, band in zip(eqmath.BAND_LABELS, BANDS_CFG):
			lst.append(getConfigListEntry("Band %s" % label, band))
		self["config"].list = lst
		self["config"].l.setList(lst)

	def changed(self):
		if self._busy:
			return
		self._busy = True
		cfg = config.plugins.hardwareeq
		cur = self["config"].getCurrent()
		if cur and cur[1] is cfg.preset:
			for key, name, gains in eqmath.PRESETS:
				if key == cfg.preset.value and gains is not None:
					for b, g in zip(BANDS_CFG, gains):
						b.value = str(g)
			self["config"].l.setList(self["config"].list)
		elif cur and cur[1] in BANDS_CFG:
			cfg.preset.value = "custom"
			self["config"].l.setList(self["config"].list)
		self._busy = False
		self.update()

	def update(self):
		res = apply_settings()
		cfg = config.plugins.hardwareeq
		if not cfg.enabled.value:
			txt = "Equalizer ist aus."
		elif res is None:
			txt = "Fehler: Treibermodul konnte nicht geladen werden."
		else:
			preamp, boost = res
			status = read_status()
			names = {"MAI(HDMI)": "HDMI", "SPDIF": "S/PDIF", "DAC": "DAC"}
			lines = status.splitlines()
			outs = [names[l.split()[0]] for l in lines if l.endswith(" EQ") and l.split()[0] in names]
			where = ", ".join(outs) if outs else "keinem Ausgang (Passthrough/Mehrkanal?)"
			if any(l.startswith("hdmi-in") and l.endswith(" EQ") for l in lines):
				where += " (auch für den HDMI-Eingang)"
			if boost > 0.05 and not cfg.protect.value:
				txt = "EQ aktiv auf %s. Anhebung bis %+.1f dB" % (where, boost)
			elif preamp < -0.05:
				txt = "EQ aktiv auf %s. Vorverstärkung %.1f dB" % (where, preamp)
			else:
				txt = "EQ aktiv auf %s." % where
		self.eq_info = txt
		self.tick()
		if cfg.agc.value != "0":
			self.timer.start(1000, False)
		else:
			self.timer.stop()

	def tick(self):
		txt = self.eq_info
		if config.plugins.hardwareeq.agc.value != "0":
			a = read_agc()
			if not a.get("reference_valid"):
				txt += "\nWerbe-Bremse: lernt den Pegel des Programms ..."
			elif a.get("gain", 0) <= -500:
				txt += "\nWerbe-Bremse: senkt gerade um %.1f dB ab" % (-a["gain"] / 1000.0)
			else:
				txt += "\nWerbe-Bremse: bereit (keine Absenkung)"
			if "loudness" in a and "reference" in a and a.get("reference_valid"):
				txt += "  [Lautheit %.0f, Bezug %.0f LUFS]" % (a["loudness"] / 1000.0, a["reference"] / 1000.0)
		self["info"].setText(txt)

	def reset(self):
		for b in BANDS_CFG:
			b.value = "0"
		config.plugins.hardwareeq.preset.value = "flat"
		config.plugins.hardwareeq.level.value = "0"
		config.plugins.hardwareeq.protect.value = False
		self["config"].l.setList(self["config"].list)
		self.update()

	def toggle(self):
		cfg = config.plugins.hardwareeq
		cfg.enabled.value = not cfg.enabled.value
		self["config"].l.setList(self["config"].list)
		self.update()

	def save(self):
		save_settings()
		self.close()

	def cancel(self):
		for x in self["config"].list:
			x[1].cancel()
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
	if cfg.enabled.value or cfg.agc.value != "0":
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
