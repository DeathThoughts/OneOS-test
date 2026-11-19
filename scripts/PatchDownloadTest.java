package com.fahadali.oneos.patch;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.KeyPairGenerator;
import java.util.ArrayDeque;
import java.util.Arrays;
import java.util.EnumMap;
import java.util.List;

public final class PatchDownloadTest {
    public static void main(String[] args) throws Exception {
        var deadlines = new EnumMap<PatchDownload.Channel, Long>(PatchDownload.Channel.class);
        var throttle = new PatchDownload.Throttle() {
            @Override public long blockedUntil(PatchDownload.Channel channel) {
                return deadlines.getOrDefault(channel, 0L);
            }

            @Override public void blockUntil(PatchDownload.Channel channel, long until) {
                deadlines.put(channel, until);
            }
        };
        long now = 1_700_000_000_000L;
        if (PatchDownload.retryAfter("120", now) != now + 120_000
                || PatchDownload.retryAfter("Tue, 14 Nov 2023 22:15:20 GMT", now) != now + 120_000
                || PatchDownload.retryAfter("invalid", now) != now + 60_000) {
            throw new AssertionError("unexpected Retry-After interpretation");
        }

        var debug = PatchDownload.Channel.DEBUG;
        var release = PatchDownload.Channel.RELEASE;
        var candidate = new GuestStartPatch.Compatibility("debug", 165, "coloros17-findx9u-in-1105",
                "1".repeat(64), "2".repeat(64));
        var otherImage = new GuestStartPatch.Compatibility("debug", 165, "other-image",
                "1".repeat(64), "2".repeat(64));
        if (!debug.url(candidate).getPath().endsWith("/oneos-patch-feed-debug/165/coloros17-findx9u-in-1105/guest-start.patch")
                || debug.url(candidate).getPath().equals(debug.url(otherImage).getPath())
                || !release.url(candidate).getPath().endsWith("/oneos-patch-feed-release/guest-start.patch")) {
            throw new AssertionError("unexpected patch feed paths");
        }
        var responses = new ArrayDeque<>(List.of(429, 500, 503, 500, 200));
        PatchDownload.Network network = url -> new Reply(url, responses.remove());
        refuses(new PatchDownload(network), debug, throttle, candidate, "patch feed is rate limited");
        if (throttle.blockedUntil(debug) <= System.currentTimeMillis() || responses.size() != 4) {
            throw new AssertionError("missing rate limit");
        }
        refuses(new PatchDownload(network), debug, throttle, candidate, "patch feed is rate limited");
        if (responses.size() != 4) {
            throw new AssertionError("cooldown made a request");
        }
        var binding = new GuestStartPatch.Compatibility("release", 159, "test", "1".repeat(64), "2".repeat(64));
        refuses(new PatchDownload(network), release, throttle, binding, "patch feed returned HTTP 500");
        if (responses.size() != 1) throw new AssertionError("wrong transient retry count");
        refuses(new PatchDownload(network), release, throttle, binding, "patch transfer failed");
        if (!responses.isEmpty()) throw new AssertionError("truncated response was not read");

        var keys = KeyPairGenerator.getInstance("Ed25519").generateKeyPair();
        byte[] envelope = GuestStartPatch.sign(new byte[]{1, 2, 3}, keys.getPrivate(), binding, 1);
        PatchDownload good = new PatchDownload(url -> new Reply(url, 200, envelope, envelope.length));
        if (!Arrays.equals(good.download(release, throttle, keys.getPublic(), binding, 0), envelope)) {
            throw new AssertionError("verified bytes changed during transfer");
        }
        envelope[envelope.length - 1] ^= 1;
        try {
            good.download(release, throttle, keys.getPublic(), binding, 0);
            throw new AssertionError("tampered response was returned");
        } catch (IOException error) {
            if (!"patch payload SHA-256 mismatch".equals(error.getMessage())) throw error;
        }

    }

    private static void refuses(PatchDownload download, PatchDownload.Channel channel,
                                PatchDownload.Throttle throttle, GuestStartPatch.Compatibility binding,
                                String expected) throws Exception {
        try {
            download.download(channel, throttle, null, binding, 0);
        } catch (IOException error) {
            if (expected.equals(error.getMessage())) return;
            throw error;
        }
        throw new AssertionError("expected " + expected);
    }

    private static final class Reply extends HttpURLConnection {
        private final int status;
        private final byte[] bytes;
        private final long declared;

        Reply(URL url, int status) {
            this(url, status, new byte[4], 5);
        }

        Reply(URL url, int status, byte[] bytes, long declared) {
            super(url);
            this.status = status;
            this.bytes = bytes;
            this.declared = declared;
        }

        @Override public int getResponseCode() { return status; }
        @Override public String getHeaderField(String name) { return "Retry-After".equals(name) ? "120" : null; }
        @Override public long getContentLengthLong() { return declared; }
        @Override public InputStream getInputStream() { return new ByteArrayInputStream(bytes); }
        @Override public void connect() {}
        @Override public void disconnect() {}
        @Override public boolean usingProxy() { return false; }
    }
}
