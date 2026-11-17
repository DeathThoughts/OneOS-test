import com.fahadali.oneos.patch.GuestStartPatch;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.EOFException;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.Signature;
import java.util.Arrays;

public final class GuestStartPatchTest {
    private static final String IMAGE_ID = "coloros-test";
    private static final String IMAGE_HASH = "a".repeat(64);
    private static final long BUILD = 157;
    private static final long SEQUENCE = 3;

    public static void main(String[] args) throws Exception {
        byte[] base = Files.readAllBytes(Path.of("switcher/assets/guest_start.sh"));
        String script = new String(base, StandardCharsets.UTF_8);
        String firstLine = "#!/system/bin/sh\n";
        if (!script.startsWith(firstLine) || !Arrays.equals(script.getBytes(StandardCharsets.UTF_8), base)) {
            throw new AssertionError("base script encoding changed");
        }
        byte[] payload = (firstLine + "echo '[OneOS patch] signed restart smoke test'\n"
                + script.substring(firstLine.length())).getBytes(StandardCharsets.UTF_8);
        KeyPairGenerator generator = KeyPairGenerator.getInstance("Ed25519");
        KeyPair key = generator.generateKeyPair();
        KeyPair otherKey = generator.generateKeyPair();
        String baseHash = GuestStartPatch.sha256(base);
        var expected = new GuestStartPatch.Compatibility("debug", BUILD, IMAGE_ID, IMAGE_HASH, baseHash);
        byte[] good = GuestStartPatch.sign(payload, key.getPrivate(), expected, SEQUENCE);
        if (!Arrays.equals(good, signed(key, baseHash, payload, payload.length, SEQUENCE))) {
            throw new AssertionError("signer changed the wire format");
        }
        var verified = GuestStartPatch.verify(new ByteArrayInputStream(good), key.getPublic(), expected, SEQUENCE - 1);
        if (verified.sequence() != SEQUENCE || !Arrays.equals(verified.payload(), payload)) {
            throw new AssertionError("valid patch failed verification");
        }

        byte[] corruptPayload = good.clone();
        corruptPayload[corruptPayload.length - 1] ^= 1;
        fails("patch payload SHA-256 mismatch", corruptPayload, key, expected, 0);
        byte[] corruptSignature = good.clone();
        corruptSignature[good.length - payload.length - 1] ^= 1;
        fails("invalid patch signature", corruptSignature, key, expected, 0);
        byte[] corruptHeader = good.clone();
        corruptHeader["ONEOS-GUEST-START-PATCH-1\n".length()] = 'x';
        fails("invalid patch signature", corruptHeader, key, expected, 0);
        fails("invalid patch signature", good, otherKey, expected, 0);
        fails("unexpected bytes after patch payload", Arrays.copyOf(good, good.length + 1), key, expected, 0);
        for (int length : new int[] {5, good.length - payload.length - 1, good.length - 1}) {
            fails(null, Arrays.copyOf(good, length), key, expected, 0);
        }
        fails("invalid patch payload description",
                signed(key, baseHash, payload, GuestStartPatch.MAX_PAYLOAD_BYTES + 1, SEQUENCE), key, expected, 0);
        fails("invalid patch number", signed(key, baseHash, payload, payload.length, 0), key, expected, 0);

        for (var incompatible : new GuestStartPatch.Compatibility[] {
                new GuestStartPatch.Compatibility("release", BUILD, IMAGE_ID, IMAGE_HASH, baseHash),
                new GuestStartPatch.Compatibility("debug", BUILD + 1, IMAGE_ID, IMAGE_HASH, baseHash),
                new GuestStartPatch.Compatibility("debug", BUILD, "other-image", IMAGE_HASH, baseHash),
                new GuestStartPatch.Compatibility("debug", BUILD, IMAGE_ID, "b".repeat(64), baseHash),
                new GuestStartPatch.Compatibility("debug", BUILD, IMAGE_ID, IMAGE_HASH, "b".repeat(64))
        }) {
            fails("incompatible patch", good, key, incompatible, 0);
        }
        fails("patch sequence already consumed", good, key, expected, SEQUENCE);
        fails("patch sequence already consumed", good, key, expected, SEQUENCE + 1);

        System.out.println("Verified signed marker fixture, tamper detection, truncated envelopes, payload limit, compatibility and sequence checks");
    }

    private static byte[] signed(KeyPair key, String baseHash, byte[] payload, int length, long sequence)
            throws Exception {
        String header = "ONEOS-GUEST-START-PATCH-1\n"
                + "debug\n" + BUILD + "\n" + IMAGE_ID + "\n" + IMAGE_HASH + "\n" + baseHash + "\n"
                + sequence + "\n" + length + " " + GuestStartPatch.sha256(payload) + "\n";
        byte[] headerBytes = header.getBytes(StandardCharsets.US_ASCII);
        Signature signer = Signature.getInstance("Ed25519");
        signer.initSign(key.getPrivate());
        signer.update(headerBytes);
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        output.write(headerBytes);
        output.write(signer.sign());
        output.write(payload);
        return output.toByteArray();
    }

    private static void fails(String message, byte[] envelope, KeyPair key, GuestStartPatch.Compatibility binding,
                              long floor) throws Exception {
        try {
            GuestStartPatch.verify(new ByteArrayInputStream(envelope), key.getPublic(), binding, floor);
        } catch (EOFException e) {
            if (message == null) return;
            throw e;
        } catch (IOException e) {
            if (e.getMessage().equals(message)) return;
            throw e;
        }
        throw new AssertionError("expected refusal: " + message);
    }
}
