import com.fahadali.oneos.patch.GuestStartPatch;
import com.fahadali.oneos.patch.PatchTrial;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyPairGenerator;
import java.util.Comparator;

public final class PatchTrialTest {
    public static void main(String[] args) throws Exception {
        Path temp = Files.createTempDirectory("oneos-patch-trial");
        try {
            Path root = Files.createDirectory(temp.resolve("host"));
            Path inbox = temp.resolve("inbox");
            var signer = KeyPairGenerator.getInstance("Ed25519").generateKeyPair();
            var target = new GuestStartPatch.Compatibility("debug", 159, "coloros-smoke",
                    "1".repeat(64), "2".repeat(64));
            var trial = new PatchTrial(root, signer.getPublic(), target);
            check(trial.select(null).equals("base"), "initial bundled fallback");

            byte[] first = envelope(signer.getPrivate(), target, 1, "first");
            Files.write(inbox, first);
            check(trial.select(inbox).equals("trial") && trial.floor() == 1, "first trial");
            check(Files.readString(root.resolve("run.sh")).equals("echo first\n"), "materialized signed bytes");
            Files.delete(inbox);
            trial.accept();
            check(trial.select(null).equals("known"), "accepted host copy survives deletion of inbox");

            Files.write(inbox, envelope(signer.getPrivate(), target, 2, "second"));
            check(trial.select(inbox).equals("trial") && trial.floor() == 2, "second trial");
            var afterInterruption = new PatchTrial(root, signer.getPublic(), target);
            check(afterInterruption.select(null).equals("known"), "rollback selection");
            check(Files.readString(root.resolve("run.sh")).equals("echo first\n"), "previous script kept");
            check(afterInterruption.select(inbox).equals("known") && afterInterruption.floor() == 2,
                    "failed or interrupted trial cannot be replayed");

            byte[] tampered = envelope(signer.getPrivate(), target, 3, "third");
            tampered[tampered.length - 1] ^= 1;
            Files.write(inbox, tampered);
            check(afterInterruption.select(inbox).equals("known") && afterInterruption.floor() == 2,
                    "invalid payload leaves floor unchanged");

            Files.delete(inbox);
            Path elsewhere = temp.resolve("elsewhere");
            Files.write(elsewhere, envelope(signer.getPrivate(), target, 3, "third"));
            Files.createSymbolicLink(inbox, elsewhere);
            check(afterInterruption.select(inbox).equals("known") && afterInterruption.floor() == 2,
                    "inbox link refused");
            Path source = Files.createDirectory(temp.resolve("source"));
            Files.copy(elsewhere, source.resolve("pending"));
            Path alias = Files.createSymbolicLink(temp.resolve("alias"), source);
            check(afterInterruption.select(alias.resolve("pending")).equals("known") && afterInterruption.floor() == 2,
                    "inbox parent link refused");
            System.out.println("Verified bundled fallback, host staging, acceptance, rollback, replay and tamper checks");
        } finally {
            try (var paths = Files.walk(temp)) {
                for (var path : paths.sorted(Comparator.reverseOrder()).toList()) Files.delete(path);
            }
        }
    }

    private static byte[] envelope(java.security.PrivateKey key, GuestStartPatch.Compatibility target,
                                   long sequence, String marker) throws Exception {
        return GuestStartPatch.sign(("echo " + marker + "\n").getBytes(StandardCharsets.US_ASCII), key, target, sequence);
    }

    private static void check(boolean passed, String test) {
        if (!passed) throw new AssertionError(test);
    }
}
