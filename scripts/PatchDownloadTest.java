package com.fahadali.oneos.patch;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.ArrayDeque;
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
        var responses = new ArrayDeque<>(List.of(429, 500, 503, 500, 200));
        PatchDownload.Network network = url -> new Reply(url, responses.remove());
        refuses(new PatchDownload(network), debug, throttle, "patch feed is rate limited");
        if (throttle.blockedUntil(debug) <= System.currentTimeMillis() || responses.size() != 4) {
            throw new AssertionError("missing rate limit");
        }
        refuses(new PatchDownload(network), debug, throttle, "patch feed is rate limited");
        if (responses.size() != 4 || debug.url().equals(release.url())) {
            throw new AssertionError("cooldown made a request or channels share a feed");
        }
        refuses(new PatchDownload(network), release, throttle, "patch feed returned HTTP 500");
        if (responses.size() != 1) throw new AssertionError("wrong transient retry count");
        refuses(new PatchDownload(network), release, throttle, "patch transfer failed");
        if (!responses.isEmpty()) throw new AssertionError("truncated response was not read");
    }

    private static void refuses(PatchDownload download, PatchDownload.Channel channel,
                                PatchDownload.Throttle throttle, String expected) throws Exception {
        try {
            download.download(channel, throttle, null, null, 0);
        } catch (IOException error) {
            if (expected.equals(error.getMessage())) return;
            throw error;
        }
        throw new AssertionError("expected " + expected);
    }

    private static final class Reply extends HttpURLConnection {
        private final int status;

        Reply(URL url, int status) {
            super(url);
            this.status = status;
        }

        @Override public int getResponseCode() { return status; }
        @Override public String getHeaderField(String name) { return "Retry-After".equals(name) ? "120" : null; }
        @Override public long getContentLengthLong() { return 5; }
        @Override public InputStream getInputStream() { return new ByteArrayInputStream(new byte[4]); }
        @Override public void connect() {}
        @Override public void disconnect() {}
        @Override public boolean usingProxy() { return false; }
    }
}
