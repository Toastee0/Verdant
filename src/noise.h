#pragma once
#include "defs.h"

// Deterministic hash → float [0,1]
float hash1(int n);

// Smooth value noise: interpolate between hashed lattice points
float vnoise(float x, int seed);

// Fractal value noise: 4 octaves
float fbm(float x, int seed);

// Triangle wave: period p, range [0,1]
float triwave(float x, float p);

// Spiky sine: |sin|^0.25 — thin base, sharp peaks
float spike(float x, float p);
