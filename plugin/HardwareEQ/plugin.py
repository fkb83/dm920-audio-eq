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


def current_gains():
	return [int(b.value) for b in BANDS_CFG]


def apply_settings():
	"""Push the configured curve into the hardware. Returns preamp in dB or None."""
	cfg = config.plugins.hardwareeq
	if not cfg.enabled.value:
		if module_loaded():
			write_proc("biquads", eqmath.flat_line())
		return None
	if not load_module():
		return None
	biquads, preamp = eqmath.design(current_gains(), int(cfg.level.value))
	if write_proc("biquads", eqmath.to_line(biquads)):
		return preamp
	return None


class HardwareEQSetup(ConfigListScreen, Screen):
	skin = """
	<screen name="HardwareEQSetup" position="center,center" size="1100,860" title="Hardware-Equalizer">
		<widget name="config" position="30,20" size="1040,630" itemHeight="45" scrollbarMode="showOnDemand" />
		<eLabel position="30,665" size="1040,2" backgroundColor="#00777777" />
		<widget source="info" render="Label" position="30,680" size="1040,90" font="Regular;28" foregroundColor="#00cccccc" />
		<eLabel position="30,800" size="15,40" backgroundColor="#00e01e1e" />
		<widget source="key_red" render="Label" position="55,795" size="230,50" font="Regular;28" valign="center" />
		<eLabel position="295,800" size="15,40" backgroundColor="#0016a016" />
		<widget source="key_green" render="Label" position="320,795" size="230,50" font="Regular;28" valign="center" />
		<eLabel position="560,800" size="15,40" backgroundColor="#00e8c81e" />
		<widget source="key_yellow" render="Label" position="585,795" size="230,50" font="Regular;28" valign="center" />
		<eLabel position="825,800" size="15,40" backgroundColor="#001e5ae0" />
		<widget source="key_blue" render="Label" position="850,795" size="230,50" font="Regular;28" valign="center" />
	</screen>"""

	def __init__(self, session):
		Screen.__init__(self, session)
		self.setTitle("Hardware-Equalizer (HDMI / S/PDIF)")
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
		self.build()
		self.onLayoutFinish.append(self.update)

	def build(self):
		cfg = config.plugins.hardwareeq
		lst = [
			getConfigListEntry("Equalizer aktiv", cfg.enabled),
			getConfigListEntry("Voreinstellung", cfg.preset),
			getConfigListEntry("Gesamtpegel (zusätzlich leiser)", cfg.level),
		]
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
		preamp = apply_settings()
		cfg = config.plugins.hardwareeq
		if not cfg.enabled.value:
			txt = "Equalizer ist aus (Ton unverändert)."
		elif preamp is None:
			txt = "Fehler: Treibermodul konnte nicht geladen werden."
		else:
			status = read_status()
			names = {"MAI(HDMI)": "HDMI", "SPDIF": "S/PDIF", "DAC": "DAC"}
			outs = [names.get(l.split()[0], l.split()[0]) for l in status.splitlines() if l.endswith(" EQ")]
			txt = "Vorverstärkung: %.1f dB (schützt vor Übersteuerung)\nAktiv auf: %s" % (
				preamp, ", ".join(outs) if outs else "keinem Ausgang (Passthrough/Mehrkanal?)")
		self["info"].setText(txt)

	def reset(self):
		for b in BANDS_CFG:
			b.value = "0"
		config.plugins.hardwareeq.preset.value = "flat"
		config.plugins.hardwareeq.level.value = "0"
		self["config"].l.setList(self["config"].list)
		self.update()

	def toggle(self):
		cfg = config.plugins.hardwareeq
		cfg.enabled.value = not cfg.enabled.value
		self["config"].l.setList(self["config"].list)
		self.update()

	def save(self):
		for x in self["config"].list:
			x[1].save()
		config.plugins.hardwareeq.save()
		configfile.save()
		self.close()

	def cancel(self):
		for x in self["config"].list:
			x[1].cancel()
		apply_settings()
		self.close()


def main(session, **kwargs):
	session.open(HardwareEQSetup)


def sessionstart(reason, **kwargs):
	if reason == 0 and config.plugins.hardwareeq.enabled.value:
		try:
			apply_settings()
		except Exception, e:
			log("autostart failed: %s" % e)


def Plugins(**kwargs):
	return [
		PluginDescriptor(where=PluginDescriptor.WHERE_SESSIONSTART, fnc=sessionstart),
		PluginDescriptor(name="Hardware-Equalizer", description="Klangregelung für HDMI- und S/PDIF-Ton",
			where=[PluginDescriptor.WHERE_PLUGINMENU, PluginDescriptor.WHERE_EXTENSIONSMENU], fnc=main),
	]
