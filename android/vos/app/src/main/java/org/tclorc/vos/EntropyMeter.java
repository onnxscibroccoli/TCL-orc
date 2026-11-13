package org.tclorc.vos;

/** Local-only informational input-rhythm indicator, NOT identity or personhood. */
public final class EntropyMeter {
    private final int[] counts = new int[6];
    private final int[] window = new int[32];
    private int samples = 0, cursor = 0;
    private long lastElapsed = -1;

    public synchronized void record(long elapsedMs) {
        if (elapsedMs < 0) return;
        if (lastElapsed >= 0 && elapsedMs > lastElapsed) {
            long dt = elapsedMs - lastElapsed;
            int bucket = dt < 80 ? 0 : dt < 160 ? 1 : dt < 320 ? 2 :
                    dt < 650 ? 3 : dt < 1500 ? 4 : 5;
            if (samples >= window.length) counts[window[cursor]]--;
            else samples++;
            window[cursor] = bucket;
            counts[bucket]++;
            cursor = (cursor + 1) % window.length;
        }
        lastElapsed = elapsedMs;
    }

    public synchronized int sampleCount() { return samples; }

    /** Normalized Shannon entropy in [0,1], 2 decimals. Suppress nothing. */
    public synchronized double normalized() {
        if (samples == 0) return 0;
        double entropy = 0;
        for (int count : counts) {
            if (count <= 0) continue;
            double p = (double) count / samples;
            entropy -= p * Math.log(p);
        }
        double normalized = entropy / Math.log(counts.length);
        return Math.round(normalized * 100) / 100.0;
    }

    public synchronized String label() {
        if (samples < 16) return "Gathering local input rhythm (no identity verification)";
        if (normalized() < 0.35) return "Repetitive controls detected; play remains unrestricted";
        return "Varied input timing; play remains unrestricted";
    }
}
