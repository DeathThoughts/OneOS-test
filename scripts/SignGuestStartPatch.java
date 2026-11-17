import com.fahadali.oneos.patch.GuestStartPatch;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.security.KeyFactory;
import java.security.spec.PKCS8EncodedKeySpec;
import java.security.spec.X509EncodedKeySpec;

public final class SignGuestStartPatch {
    public static void main(String[] args) throws Exception {
        if (args.length != 9) {
            throw new IllegalArgumentException(
                    "Usage: SignGuestStartPatch <channel> <host-build> <image-id> <image-sha256> "
                            + "<sequence> <base-script> <replacement-script> <key-directory> <output>");
        }
        Path keys = Path.of(args[7]);
        KeyFactory factory = KeyFactory.getInstance("Ed25519");
        var privateKey = factory.generatePrivate(new PKCS8EncodedKeySpec(read(keys.resolve("private.der"), 1024)));
        var publicKey = factory.generatePublic(new X509EncodedKeySpec(read(keys.resolve("public.der"), 1024)));
        long sequence = Long.parseLong(args[4]);
        int limit = GuestStartPatch.MAX_PAYLOAD_BYTES;
        var target = new GuestStartPatch.Compatibility(args[0], Long.parseLong(args[1]), args[2], args[3],
                GuestStartPatch.sha256(read(Path.of(args[5]), limit)));
        byte[] envelope = GuestStartPatch.sign(read(Path.of(args[6]), limit), privateKey, target, sequence);
        // Check the output against the public key intended for this channel before publishing bytes.
        GuestStartPatch.verify(new ByteArrayInputStream(envelope), publicKey, target, sequence - 1);

        Path output = Path.of(args[8]).toAbsolutePath();
        Path temp = Files.createTempFile(output.getParent(), ".guest-start-patch-", ".tmp");
        try {
            Files.write(temp, envelope);
            Files.move(temp, output, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
        } finally {
            Files.deleteIfExists(temp);
        }
    }

    private static byte[] read(Path path, int limit) throws IOException {
        try (var input = Files.newInputStream(path)) {
            byte[] data = input.readNBytes(limit + 1);
            if (data.length > limit) throw new IOException("Input exceeds size limit: " + path.getFileName());
            return data;
        }
    }
}
