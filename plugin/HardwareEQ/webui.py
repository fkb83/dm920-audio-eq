# -*- coding: utf-8 -*-
# Web page for the Hardware-Equalizer: http://<box>/eq
# Runs inside Enigma2 (Twisted, main thread), so it can use the config and the plugin functions directly.
import os
import json

from twisted.web import resource, static
from Components.config import config

import eqmath
import plugin as eq

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
_registered = False


def _state(session):
	cfg = config.plugins.hardwareeq
	agc = eq.read_agc()
	status = eq.read_status()
	outs = []
	names = {"MAI(HDMI)": "HDMI", "SPDIF": "S/PDIF", "DAC": "Analog"}
	hdmi_in_eq = False
	for line in status.splitlines():
		parts = line.split()
		if len(parts) >= 2 and parts[-1] == "EQ" and parts[0] in names:
			outs.append(names[parts[0]])
		if line.startswith("hdmi-in") and line.endswith("EQ"):
			hdmi_in_eq = True
	return {
		"enabled": cfg.enabled.value,
		"preset": cfg.preset.value,
		"level": int(cfg.level.value),
		"protect": cfg.protect.value,
		"agc": int(cfg.agc.value),
		"radio_hdmi": cfg.radio_hdmi.value,
		"bands": eq.current_gains(),
		"band_labels": eqmath.BAND_LABELS,
		"band_defs": [[k, f, q] for k, f, q in eqmath.BANDS],
		"presets": [[k, n, g] for k, n, g in eqmath.PRESETS],
		"gain_range": [eqmath.GAIN_MIN, eqmath.GAIN_MAX],
		"module": eq.module_loaded(),
		"outputs": outs,
		"hdmi_available": eq.hdmi_available(),
		"source": "hdmi" if eq.hdmi_active(session) else "tv",
		"hdmi_in_eq": hdmi_in_eq,
		"agc_live": {
			"loudness": agc.get("loudness", 0) / 1000.0,
			"reference": agc.get("reference", 0) / 1000.0,
			"gain": agc.get("gain", 0) / 1000.0,
			"valid": bool(agc.get("reference_valid", 0)),
		},
	}


def _apply(data):
	cfg = config.plugins.hardwareeq
	if "preset" in data:
		eq.set_preset(str(data["preset"]))
	if "bands" in data:
		gains = [max(eqmath.GAIN_MIN, min(eqmath.GAIN_MAX, int(round(float(g))))) for g in data["bands"]][:len(eq.BANDS_CFG)]
		for b, g in zip(eq.BANDS_CFG, gains):
			b.value = str(g)
		cfg.preset.value = "custom"
		for k, n, g in eqmath.PRESETS:          # show the preset name again if the curve matches one
			if g is not None and list(g) == eq.current_gains():
				cfg.preset.value = k
				break
	if "level" in data:
		cfg.level.value = str(max(-12, min(0, int(data["level"]))))
	if "enabled" in data:
		cfg.enabled.value = bool(data["enabled"])
	if "protect" in data:
		cfg.protect.value = bool(data["protect"])
	if "agc" in data:
		cfg.agc.value = str(max(0, min(3, int(data["agc"]))))
	if "radio_hdmi" in data:
		cfg.radio_hdmi.value = bool(data["radio_hdmi"])
	eq.apply_settings()
	if data.get("save"):
		eq.save_settings()


class Api(resource.Resource):
	isLeaf = True

	def __init__(self, session):
		resource.Resource.__init__(self)
		self.session = session

	def _json(self, request, obj, code=200):
		request.setResponseCode(code)
		request.setHeader("Content-Type", "application/json; charset=utf-8")
		request.setHeader("Cache-Control", "no-store")
		return json.dumps(obj)

	def render_GET(self, request):
		return self._json(request, _state(self.session))

	def render_POST(self, request):
		try:
			data = json.loads(request.content.read() or "{}")
			what = request.postpath[0] if request.postpath else "set"
			if what == "source":
				want = "hdmi" if data.get("source") == "hdmi" else "tv"
				ok = eq.open_hdmi(self.session) if want == "hdmi" else eq.close_hdmi(self.session)
				if not ok:
					return self._json(request, {"error": "Umschalten nicht möglich (Menü offen?)"}, 409)
				st = _state(self.session)
				st["source"] = want          # the screen opens/closes asynchronously
				return self._json(request, st)
			_apply(data)
			return self._json(request, _state(self.session))
		except Exception, e:
			eq.log("web api error: %s" % e)
			return self._json(request, {"error": str(e)}, 400)


class Root(resource.Resource):
	def __init__(self, session):
		resource.Resource.__init__(self)
		self.putChild("api", Api(session))
		self.putChild("static", static.File(WEB_DIR))

	def getChild(self, path, request):
		if path in ("", "index.html"):
			return static.File(os.path.join(WEB_DIR, "index.html"), defaultType="text/html")
		return resource.Resource.getChild(self, path, request)

	def render_GET(self, request):
		request.redirect("/eq/")
		return ""


def register(session):
	global _registered
	if _registered:
		return
	from Plugins.Extensions.WebInterface.WebChilds.Toplevel import addExternalChild
	addExternalChild(("eq", Root(session), "Hardware-EQ", "1", True))
	_registered = True
	eq.log("web page registered at /eq")
