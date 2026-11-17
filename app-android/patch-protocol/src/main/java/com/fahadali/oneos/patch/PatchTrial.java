package com.fahadali.oneos.patch;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.SecureDirectoryStream;
import java.nio.file.attribute.BasicFileAttributeView;
import java.util.Set;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.security.GeneralSecurityException;
import java.security.PublicKey;

/** Host-only state. The caller owns the directory and serializes restart operations. */
public final class PatchTrial {
    private final Path state;
    private final PublicKey key;
    private final GuestStartPatch.Compatibility target;

    public PatchTrial(Path state, PublicKey key, GuestStartPatch.Compatibility target) {
        this.state = state;
        this.key = key;
        this.target = target;
    }

    /**
     * Selects a script for this restart. Consuming a new sequence precedes returning
     * its executable payload, so an interrupted trial cannot be tried again.
     */
    public String select(Path inbox) throws IOException, GeneralSecurityException {
        Files.deleteIfExists(state.resolve("trial.patch"));
        Files.deleteIfExists(state.resolve("run.sh"));
        if (inbox != null && !Files.notExists(inbox, LinkOption.NOFOLLOW_LINKS)) {
            try {
                return stage(inbox);
            } catch (IOException | GeneralSecurityException refused) {
                System.err.println("OneOS patch: candidate refused; using the previous script");
            }
        }
        Path known = state.resolve("known.patch");
        if (Files.isRegularFile(known, LinkOption.NOFOLLOW_LINKS)) {
            var patch = verify(read(known), 0);
            write(state.resolve("run.sh"), patch.payload());
            return "known";
        }
        return "base";
    }

    private String stage(Path inbox) throws IOException, GeneralSecurityException {
        Path trial = state.resolve("trial.patch");
        byte[] snapshot = readInbox(inbox);
        write(trial, snapshot);
        try {
            var patch = verify(read(trial), floor());
            write(state.resolve("floor"), Long.toString(patch.sequence()).getBytes(java.nio.charset.StandardCharsets.US_ASCII));
            write(state.resolve("run.sh"), patch.payload());
            return "trial";
        } catch (IOException | GeneralSecurityException refused) {
            Files.deleteIfExists(trial);
            throw refused;
        }
    }

    public void accept() throws IOException, GeneralSecurityException {
        Path trial = state.resolve("trial.patch");
        var patch = verify(read(trial), 0);
        if (patch.sequence() != floor()) throw new IOException("trial sequence changed");
        Files.move(trial, state.resolve("known.patch"), StandardCopyOption.ATOMIC_MOVE,
                StandardCopyOption.REPLACE_EXISTING);
        syncDirectory(state);
    }

    public long floor() throws IOException {
        Path floor = state.resolve("floor");
        if (Files.notExists(floor, LinkOption.NOFOLLOW_LINKS)) return 0;
        byte[] raw = read(floor, 19);
        String value = new String(raw, java.nio.charset.StandardCharsets.US_ASCII);
        if (!value.matches("[1-9][0-9]{0,18}")) throw new IOException("invalid replay floor");
        try {
            return Long.parseLong(value);
        } catch (NumberFormatException invalid) {
            throw new IOException("invalid replay floor");
        }
    }

    private GuestStartPatch.Verified verify(byte[] envelope, long floor)
            throws IOException, GeneralSecurityException {
        return GuestStartPatch.verify(new ByteArrayInputStream(envelope), key, target, floor);
    }

    private static byte[] read(Path path) throws IOException {
        return read(path, GuestStartPatch.MAX_ENVELOPE_BYTES);
    }

    private static byte[] readInbox(Path inbox) throws IOException {
        Path absolute = inbox.toAbsolutePath().normalize();
        try (var stream = Files.newDirectoryStream(absolute.getRoot())) {
            if (!(stream instanceof SecureDirectoryStream<Path> secure)) {
                throw new IOException("secure patch inbox access is unavailable");
            }
            return readInboxEntry(secure, absolute, 0);
        }
    }

    private static byte[] readInboxEntry(SecureDirectoryStream<Path> directory, Path absolute, int index)
            throws IOException {
        Path name = absolute.getName(index);
        if (index < absolute.getNameCount() - 1) {
            try (var child = directory.newDirectoryStream(name, LinkOption.NOFOLLOW_LINKS)) {
                return readInboxEntry(child, absolute, index + 1);
            }
        }
        var attributes = directory.getFileAttributeView(name, BasicFileAttributeView.class, LinkOption.NOFOLLOW_LINKS);
        if (!attributes.readAttributes().isRegularFile()) throw new IOException("patch inbox is not a regular file");
        try (var channel = directory.newByteChannel(name, Set.of(StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS));
             var input = java.nio.channels.Channels.newInputStream(channel)) {
            byte[] data = input.readNBytes(GuestStartPatch.MAX_ENVELOPE_BYTES + 1);
            if (data.length > GuestStartPatch.MAX_ENVELOPE_BYTES) throw new IOException("patch inbox exceeds size limit");
            return data;
        }
    }

    private static byte[] read(Path path, int limit) throws IOException {
        if (!Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS)) throw new IOException("expected a regular file");
        try (var channel = Files.newByteChannel(path, StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS);
             var input = java.nio.channels.Channels.newInputStream(channel)) {
            byte[] data = input.readNBytes(limit + 1);
            if (data.length > limit) throw new IOException("patch file exceeds size limit");
            return data;
        }
    }

    private static void write(Path target, byte[] bytes) throws IOException {
        Path dir = target.getParent();
        Path part = Files.createTempFile(dir, ".oneos-patch-", ".tmp");
        try {
            try (var channel = FileChannel.open(part, StandardOpenOption.WRITE)) {
                ByteBuffer data = ByteBuffer.wrap(bytes);
                while (data.hasRemaining()) channel.write(data);
                channel.force(true);
            }
            Files.move(part, target, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
            syncDirectory(dir);
        } finally {
            Files.deleteIfExists(part);
        }
    }

    private static void syncDirectory(Path directory) throws IOException {
        try (var channel = FileChannel.open(directory, StandardOpenOption.READ)) {
            channel.force(true);
        }
    }
}
