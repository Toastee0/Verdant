#include "noise.h"

float hash1(int n) {
    n = (n << 13) ^ n;
    n = n * (n * n * 15731 + 789221) + 1376312589;
    return (float)(n & 0x7fffffff) / (float)0x7fffffff;
}

float vnoise(float x, int seed) {
    int   ix = (int)floorf(x);
    float fx = x - (float)ix;
    float t  = fx * fx * (3.0f - 2.0f * fx);  // smoothstep
    float a  = hash1(ix * 1619 + seed);
    float b  = hash1((ix + 1) * 1619 + seed);
    return a + t * (b - a);
}

float fbm(float x, int seed) {
    float v = 0.0f, amp = 0.5f, freq = 1.0f;
    for (int o = 0; o < 4; o++) {
        v    += vnoise(x * freq, seed + o * 997) * amp;
        freq *= 2.1f;
        amp  *= 0.5f;
    }
    return v;   // ~[0,1]
}

float triwave(float x, float p) {
    float t = fmodf(x / p, 1.0f);
    return (t < 0.5f) ? (2.0f * t) : (2.0f - 2.0f * t);
}

float spike(float x, float p) {
    float s = sinf(x * (float)M_PI * 2.0f / p);
    s = s < 0.0f ? -s : s;    // |sin|
    return powf(s, 0.25f);    // sharpen toward 1, crush near 0
}
