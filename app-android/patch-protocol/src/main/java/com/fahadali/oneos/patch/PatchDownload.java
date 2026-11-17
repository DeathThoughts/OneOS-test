package com.fahadali.oneos.patch;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.GeneralSecurityException;
import java.security.PublicKey;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeParseException;

/** Bounded fetch of the signed envelope. The caller persists per-channel throttling. */
public final class PatchDownload {
    public enum Channel {
        DEBUG("oneos-patch-feed-debug"), RELEASE("oneos-patch-feed-release");

        private final String branch;

        Channel(String branch) {
            this.branch = branch;
        }

        URL url() throws IOException {
            return new URL("https://raw.githubusercontent.com/DeathThoughts/OneOS-test/"
                    + branch + "/guest-start.patch");
        }
    }

    public interface Throttle {
        long blockedUntil(Channel channel);
        void blockUntil(Channel channel, long epochMillis) throws IOException;
    }

    interface Network {
        HttpURLConnection connect(URL url) throws IOException;
    }

    private static final int REQUEST_TIMEOUT_MS = 4000;
    private static final int TRANSFER_TIMEOUT_MS = 24000;
    private static final int MAX_ATTEMPTS = 3;
    private final Network network;

    public PatchDownload() {
        this(url -> (HttpURLConnection) url.openConnection());
    }

    PatchDownload(Network network) {
        this.network = network;
    }

    /**
     * Returns the exact verified envelope for atomic staging by the caller.
     * This operation never writes to an inbox or requests a restart.
     */
    public synchronized byte[] download(Channel channel, Throttle throttle, PublicKey key,
                                        GuestStartPatch.Compatibility compatibility, long sequenceFloor)
            throws IOException, GeneralSecurityException, InterruptedException {
        if (throttle.blockedUntil(channel) > System.currentTimeMillis()) {
            throw new IOException("patch feed is rate limited");
        }
        long start = System.nanoTime();
        for (int attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
            int status = 0;
            byte[] envelope = null;
            HttpURLConnection connection = null;
            try {
                connection = network.connect(channel.url());
                connection.setInstanceFollowRedirects(false);
                connection.setUseCaches(false);
                int requestTimeout = Math.min(REQUEST_TIMEOUT_MS, remainingMillis(start));
                connection.setConnectTimeout(requestTimeout);
                connection.setReadTimeout(requestTimeout);
                connection.setRequestProperty("Accept-Encoding", "identity");
                status = connection.getResponseCode();
                if (status == 200) {
                    long length = connection.getContentLengthLong();
                    if (length > GuestStartPatch.MAX_ENVELOPE_BYTES) {
                        throw new IOException("patch response exceeds size limit");
                    }
                    try (InputStream input = connection.getInputStream()) {
                        envelope = read(input, length, start);
                    }
                }
            } catch (IOException failure) {
                // The connection exception could contain its URL. Use a fixed message.
                throw new IOException("patch transfer failed");
            } finally {
                if (status == 429 && connection != null) {
                    long until = retryAfter(connection.getHeaderField("Retry-After"),
                            System.currentTimeMillis());
                    try {
                        throttle.blockUntil(channel, until);
                    } finally {
                        connection.disconnect();
                    }
                } else if (connection != null) {
                    connection.disconnect();
                }
            }
            if (envelope != null) {
                GuestStartPatch.verify(new ByteArrayInputStream(envelope), key, compatibility, sequenceFloor);
                return envelope;
            }
            if (status == 429) {
                throw new IOException("patch feed is rate limited");
            }
            if (!isTransient(status) || attempt + 1 == MAX_ATTEMPTS) {
                throw new IOException("patch feed returned HTTP " + status);
            }
            Thread.sleep(Math.min(500L << attempt, remainingMillis(start)));
        }
        throw new IOException("patch transfer exhausted retries");
    }

    private static byte[] read(InputStream input, long expectedLength, long start) throws IOException {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        byte[] chunk = new byte[8192];
        while (true) {
            remainingMillis(start);
            int count = input.read(chunk);
            if (count == -1) {
                break;
            }
            if (count > GuestStartPatch.MAX_ENVELOPE_BYTES - output.size()) {
                throw new IOException("patch response exceeds size limit");
            }
            output.write(chunk, 0, count);
        }
        if (expectedLength >= 0 && output.size() != expectedLength) {
            throw new IOException("incomplete patch response");
        }
        return output.toByteArray();
    }

    private static int remainingMillis(long start) throws IOException {
        long remaining = TRANSFER_TIMEOUT_MS - (System.nanoTime() - start) / 1_000_000;
        if (remaining <= 0) {
            throw new IOException("patch transfer timed out");
        }
        return (int) remaining;
    }

    private static boolean isTransient(int status) {
        return status == 500 || status == 502 || status == 503 || status == 504;
    }

    static long retryAfter(String header, long now) {
        if (header != null) {
            String value = header.trim();
            if (value.matches("[0-9]+")) {
                try {
                    return Math.addExact(now, Math.multiplyExact(Long.parseLong(value), 1000L));
                } catch (ArithmeticException | NumberFormatException tooLarge) {
                    return Long.MAX_VALUE;
                }
            }
            try {
                long date = ZonedDateTime.parse(value, DateTimeFormatter.RFC_1123_DATE_TIME)
                        .toInstant().toEpochMilli();
                return Math.max(now, date);
            } catch (DateTimeParseException invalid) {
                // Use the default below when the server does not send a usable value.
            }
        }
        return now + 60_000;
    }
}
