package com.fahadali.oneos.patch;

import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.security.PublicKey;
import java.security.Signature;
import java.security.SignatureException;

/**
 * Wire format for a complete replacement of guest_start.sh.
 *
 * Sign the ASCII header (all eight lines including their trailing LF), then append
 * the 64 raw Ed25519 signature bytes and exactly payloadSize bytes. The SHA-256
 * in the authenticated header covers the entire payload. The caller supplies the
 * pinned key, live compatibility information, and the highest consumed sequence.
 */
public final class GuestStartPatch {
    public static final int MAX_PAYLOAD_BYTES = 512 * 1024;
    public static final int MAX_ENVELOPE_BYTES = MAX_PAYLOAD_BYTES + 8 * 129 + 64;
    private static final String MAGIC = "ONEOS-GUEST-START-PATCH-1";
    private static final int SIGNATURE_BYTES = 64;
    private static final char[] HEX = "0123456789abcdef".toCharArray();

    private GuestStartPatch() {}

    public static final class Compatibility {
        private final String channel;
        private final long hostBuild;
        private final String imageId;
        private final String imageSha256;
        private final String baseSha256;

        public Compatibility(String channel, long hostBuild, String imageId, String imageSha256,
                             String baseSha256) {
            if (!("debug".equals(channel) || "release".equals(channel)) || hostBuild < 1
                    || !imageId.matches("[a-z0-9][a-z0-9._-]{0,95}")
                    || !isSha256(imageSha256) || !isSha256(baseSha256)) {
                throw new IllegalArgumentException("invalid patch compatibility");
            }
            this.channel = channel;
            this.hostBuild = hostBuild;
            this.imageId = imageId;
            this.imageSha256 = imageSha256;
            this.baseSha256 = baseSha256;
        }
    }

    public static final class Verified {
        private final long sequence;
        private final byte[] payload;

        private Verified(long sequence, byte[] payload) {
            this.sequence = sequence;
            this.payload = payload;
        }

        public long sequence() { return sequence; }
        public byte[] payload() { return payload.clone(); }
    }

    public static Verified verify(InputStream source, PublicKey pinnedKey, Compatibility expected,
                                  long highestConsumed) throws IOException, GeneralSecurityException {
        if (highestConsumed < 0) {
            throw new IllegalArgumentException("invalid patch sequence floor");
        }
        DataInputStream input = new DataInputStream(source);
        ByteArrayOutputStream header = new ByteArrayOutputStream();
        require(MAGIC.equals(line(input, header)), "invalid patch format");
        String channel = line(input, header);
        long build = positiveNumber(line(input, header));
        String image = line(input, header);
        String imageHash = line(input, header);
        String baseHash = line(input, header);
        long sequence = positiveNumber(line(input, header));
        String sizeAndHash = line(input, header);
        int separator = sizeAndHash.indexOf(' ');
        require(separator > 0, "invalid patch payload description");
        long size = positiveNumber(sizeAndHash.substring(0, separator));
        String payloadHash = sizeAndHash.substring(separator + 1);
        require(size <= MAX_PAYLOAD_BYTES && isSha256(payloadHash), "invalid patch payload description");

        byte[] signatureBytes = new byte[SIGNATURE_BYTES];
        input.readFully(signatureBytes);
        Signature signature = Signature.getInstance("Ed25519");
        signature.initVerify(pinnedKey);
        signature.update(header.toByteArray());
        boolean authenticated;
        try {
            authenticated = signature.verify(signatureBytes);
        } catch (SignatureException e) {
            throw new IOException("invalid patch signature");
        }
        require(authenticated, "invalid patch signature");

        require(expected.channel.equals(channel) && expected.hostBuild == build
                && expected.imageId.equals(image) && expected.imageSha256.equals(imageHash)
                && expected.baseSha256.equals(baseHash), "incompatible patch");
        require(sequence > highestConsumed, "patch sequence already consumed");

        byte[] payload = new byte[(int) size];
        input.readFully(payload);
        require(input.read() == -1, "unexpected bytes after patch payload");
        require(sha256(payload).equals(payloadHash), "patch payload SHA-256 mismatch");
        return new Verified(sequence, payload);
    }

    public static String sha256(byte[] contents) throws GeneralSecurityException {
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(contents);
        char[] output = new char[digest.length * 2];
        for (int i = 0; i < digest.length; i++) {
            int value = digest[i] & 255;
            output[i * 2] = HEX[value >>> 4];
            output[i * 2 + 1] = HEX[value & 15];
        }
        return new String(output);
    }

    private static String line(DataInputStream input, ByteArrayOutputStream header) throws IOException {
        ByteArrayOutputStream line = new ByteArrayOutputStream();
        for (int i = 0; i <= 128; i++) {
            int value = input.readUnsignedByte();
            header.write(value);
            if (value == '\n') {
                return new String(line.toByteArray(), StandardCharsets.US_ASCII);
            }
            require(value >= 32 && value <= 126, "non-ASCII patch header");
            line.write(value);
        }
        throw new IOException("patch header line too long");
    }

    private static long positiveNumber(String value) throws IOException {
        require(value.matches("[1-9][0-9]{0,18}"), "invalid patch number");
        try {
            return Long.parseLong(value);
        } catch (NumberFormatException e) {
            throw new IOException("invalid patch number");
        }
    }

    private static boolean isSha256(String value) {
        return value.matches("[0-9a-f]{64}");
    }

    private static void require(boolean condition, String message) throws IOException {
        if (!condition) {
            throw new IOException(message);
        }
    }
}
