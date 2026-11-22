# -*- coding: utf-8 -*-
# Filter design for the DM920 hardware equalizer (BAPE custom stage).
# Hardware form per biquad: y = b0*x + b1*x1 + b2*x2 + a1*y1 + a2*y2, Q5.26 fixed point.
# Works with Python 2.7 (box) and Python 3 (tests).
import math
import cmath

FS = 48000.0
Q26 = 1 << 26
MAX_BIQUADS = 8

# (type, frequency, Q) for the 8 bands
BANDS = [
	("lowshelf", 60.0, 0.707),
	("peak", 150.0, 1.1),
	("peak", 400.0, 1.1),
	("peak", 1000.0, 1.1),
	("peak", 2400.0, 1.1),
	("peak", 6000.0, 1.1),
	("peak", 10000.0, 1.1),
	("highshelf", 15000.0, 0.707),
]
BAND_LABELS = ["60 Hz", "150 Hz", "400 Hz", "1 kHz", "2,4 kHz", "6 kHz", "10 kHz", "15 kHz"]
GAIN_MIN, GAIN_MAX = -12, 12

PRESETS = [
	("flat", "Neutral", [0, 0, 0, 0, 0, 0, 0, 0]),
	("bass", "Mehr Bass", [6, 4, 1, 0, 0, 0, 0, 0]),
	("speech", "Sprache / Dialog", [-4, -2, 0, 2, 4, 3, 0, -2]),
	("loudness", "Loudness (leise hören)", [6, 3, 0, -1, 0, 2, 4, 4]),
	("treble", "Mehr Höhen", [0, 0, 0, 0, 1, 3, 4, 5]),
	("night", "Nachtmodus (weniger Bass)", [-8, -5, -2, 0, 2, 1, 0, 0]),
	("custom", "Eigene Einstellung", None),
]


def rbj(kind, f, gain_db, q):
	"""RBJ cookbook biquad, returned in hardware form [b0, b1, b2, a1, a2]."""
	A = 10.0 ** (gain_db / 40.0)
	w = 2.0 * math.pi * f / FS
	c = math.cos(w)
	s = math.sin(w)
	al = s / (2.0 * q)
	if kind == "peak":
		b = [1 + al * A, -2 * c, 1 - al * A]
		a = [1 + al / A, -2 * c, 1 - al / A]
	elif kind == "lowshelf":
		sq = 2 * math.sqrt(A) * al
		b = [A * ((A + 1) - (A - 1) * c + sq), 2 * A * ((A - 1) - (A + 1) * c), A * ((A + 1) - (A - 1) * c - sq)]
		a = [(A + 1) + (A - 1) * c + sq, -2 * ((A - 1) + (A + 1) * c), (A + 1) + (A - 1) * c - sq]
	elif kind == "highshelf":
		sq = 2 * math.sqrt(A) * al
		b = [A * ((A + 1) + (A - 1) * c + sq), -2 * A * ((A - 1) + (A + 1) * c), A * ((A + 1) + (A - 1) * c - sq)]
		a = [(A + 1) - (A - 1) * c + sq, 2 * ((A - 1) - (A + 1) * c), (A + 1) - (A - 1) * c - sq]
	else:
		raise ValueError(kind)
	return [b[0] / a[0], b[1] / a[0], b[2] / a[0], -a[1] / a[0], -a[2] / a[0]]


def response(biquads, f):
	z = cmath.exp(-2j * math.pi * f / FS)
	h = 1.0
	for b0, b1, b2, a1, a2 in biquads:
		h *= (b0 + b1 * z + b2 * z * z) / (1 - a1 * z - a2 * z * z)
	return abs(h)


def _freqs():
	f = 15.0
	while f < 23000.0:
		yield f
		f *= 1.02


def max_gain_db(biquads):
	return 20.0 * math.log10(max(response(biquads, f) for f in _freqs()))


def design(gains, level_db=0.0, protect=False):
	"""Return (biquads, preamp_db).

	Default: a "real" EQ - boosting a band makes that band louder, everything else stays as it is.
	protect=True adds a negative preamp so that the total gain never exceeds 0 dB
	(no clipping, but boosting then lowers the rest)."""
	bq = []
	for (kind, f, q), g in zip(BANDS, gains):
		if g:
			bq.append(rbj(kind, f, float(g), q))
	if not bq:
		bq = [[1.0, 0.0, 0.0, 0.0, 0.0]]
	preamp = 0.0
	if protect:
		peak = max_gain_db(bq)
		preamp = -max(0.0, peak) - 0.1 if peak > 0 else 0.0   # small safety margin
	preamp += min(0.0, float(level_db))
	lin = 10.0 ** (preamp / 20.0)
	bq[0] = [bq[0][0] * lin, bq[0][1] * lin, bq[0][2] * lin, bq[0][3], bq[0][4]]
	return bq, preamp


def to_line(biquads):
	vals = []
	for q5 in biquads:
		for c in q5:
			v = int(round(c * Q26))
			if v > 0x7fffffff or v < -0x80000000:
				raise ValueError("coefficient out of range")
			vals.append(str(v))
	return "%d %s" % (len(biquads), " ".join(vals))


def flat_line():
	return to_line([[1.0, 0.0, 0.0, 0.0, 0.0]])
