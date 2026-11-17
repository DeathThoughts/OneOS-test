package com.fahadali.oneos.guest;

import com.fahadali.oneos.patch.GuestStartPatch;
import com.fahadali.oneos.patch.PatchTrial;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyFactory;
import java.security.PublicKey;
import java.security.spec.X509EncodedKeySpec;
import java.util.Base64;

/** Entry point for the host's app_process, loaded from the staged, hash-checked guest APK. */
public final class GuestPatchTool {
    public static String channel() { return BuildConfig.DEBUG ? "debug" : "release"; }

    public static PublicKey key() throws java.security.GeneralSecurityException {
        return KeyFactory.getInstance("Ed25519").generatePublic(
                new X509EncodedKeySpec(Base64.getDecoder().decode(BuildConfig.SIGNED_PATCH_PUBLIC_KEY)));
    }

    public static GuestStartPatch.Compatibility compatibility(String[] target) {
        if (target.length != 5 || !channel().equals(target[0])) {
            throw new IllegalArgumentException("patch channel or compatibility missing");
        }
        return new GuestStartPatch.Compatibility(target[0], Long.parseLong(target[1]), target[2], target[3], target[4]);
    }

    public static void main(String[] args) {
        try {
            PublicKey pinned = key();
            if (args.length == 1 && "channel".equals(args[0])) {
                System.out.println(channel());
                return;
            }
            if (args.length < 2 || args.length > 3) throw new IllegalArgumentException("invalid patch command");
            Path directory = Path.of(args[1]);
            String[] target = Files.readString(directory.resolve("target")).strip().split(" ");
            var binding = compatibility(target);
            var trial = new PatchTrial(directory, pinned, binding);
            switch (args[0]) {
                case "select":
                    System.out.println(trial.select(args.length == 3 ? Path.of(args[2]) : null));
                    break;
                case "accept":
                    trial.accept();
                    break;
                case "floor":
                    System.out.println(trial.floor());
                    break;
                default:
                    throw new IllegalArgumentException("invalid patch command");
            }
        } catch (Exception refused) {
            System.err.println("OneOS patch: host verifier unavailable or state refused");
            System.exit(1);
        }
    }
}
