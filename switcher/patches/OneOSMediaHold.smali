.class public final Lcom/android/server/OneOSMediaHold;
.super Ljava/lang/Object;
.source "OneOSMediaHold.java"

# OneOS (switcher/patches/guest_media_mount_hold.py): StorageManagerService's
# onVolumeStateChangedAsync calls await() right before it tells MediaProvider
# that a volume changed state. MediaProvider answers the emulated volume's
# MOUNTED with a full scan, and in the guest that scan ran a few seconds before
# guest_start.sh had bound One UI's albums into /data/media/0/OneUI: it found
# OneUI/ empty, dropped every row and indexed them all again (S25 Ultra,
# ColorOS 17 coloros17-findx9u-in-1105, session a12bf19e1931e81a: 4560 items
# at 25 s, 0 at 38 s, ~100 s of re-indexing).
#
# For user 0's emulated volume only, once per system_server, and only when
# guest_start.sh set oneos.media.hold=1: wait (at most 15 s, 100 ms steps) for
# oneos.media.published=1, which guest_start.sh sets once the albums are
# mounted. The caller holds no lock here (after mLock's monitor-exit). Nothing
# in here can throw: a failure only means no wait.
#
# Hand-written smali with no Java source (the class is small, and the SDK that
# a build like scripts/build-guest-lock-smali.sh needs was not at hand).

.field private static sWaited:Z


# vol: the volume (com.android.server.storage.WatchedVolumeInfo, or
# android.os.storage.VolumeInfo on a framework whose onVolumeStateChangedAsync
# takes that; the patcher writes the caller's type in). oldState is unused: it
# is there so the call can take p1 .. p3 as a range whatever the caller's
# register count.
.method public static await(Lcom/android/server/storage/WatchedVolumeInfo;II)V
    .locals 9
    .param p0, "vol"    # Lcom/android/server/storage/WatchedVolumeInfo;
    .param p1, "oldState"    # I
    .param p2, "newState"    # I

    # The start time, a long before the try: both handlers see it as one.
    const-wide/16 v3, 0x0

    :try_start_0
    # VolumeInfo.STATE_MOUNTED
    const/4 v0, 0x2

    if-ne p2, v0, :done

    if-eqz p0, :done

    # VolumeInfo.TYPE_EMULATED
    invoke-virtual {p0}, Lcom/android/server/storage/WatchedVolumeInfo;->getType()I

    move-result v1

    if-ne v1, v0, :done

    invoke-virtual {p0}, Lcom/android/server/storage/WatchedVolumeInfo;->getMountUserId()I

    move-result v1

    if-nez v1, :done

    invoke-virtual {p0}, Lcom/android/server/storage/WatchedVolumeInfo;->getId()Ljava/lang/String;

    move-result-object v1

    if-eqz v1, :done

    const-string v2, "emulated"

    invoke-virtual {v1, v2}, Ljava/lang/String;->startsWith(Ljava/lang/String;)Z

    move-result v1

    if-eqz v1, :done

    # Once per system_server: a later remount (a user switch) is not held.
    sget-boolean v1, Lcom/android/server/OneOSMediaHold;->sWaited:Z

    if-nez v1, :done

    const-string v1, "1"

    const-string v0, "oneos.media.hold"

    invoke-static {v0}, Landroid/os/SystemProperties;->get(Ljava/lang/String;)Ljava/lang/String;

    move-result-object v0

    invoke-virtual {v1, v0}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z

    move-result v0

    if-eqz v0, :done

    const/4 v0, 0x1

    sput-boolean v0, Lcom/android/server/OneOSMediaHold;->sWaited:Z

    invoke-static {}, Landroid/os/SystemClock;->elapsedRealtime()J

    move-result-wide v3

    :poll
    const-string v0, "oneos.media.published"

    invoke-static {v0}, Landroid/os/SystemProperties;->get(Ljava/lang/String;)Ljava/lang/String;

    move-result-object v0

    invoke-virtual {v1, v0}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z

    move-result v0

    if-nez v0, :published

    invoke-static {}, Landroid/os/SystemClock;->elapsedRealtime()J

    move-result-wide v5

    sub-long/2addr v5, v3

    # 15000 ms at most
    const-wide/16 v7, 0x3a98

    cmp-long v0, v5, v7

    if-gez v0, :timed_out

    const-wide/16 v5, 0x64

    invoke-static {v5, v6}, Ljava/lang/Thread;->sleep(J)V

    goto :poll

    :published
    const-string v2, "published"

    goto :log

    :timed_out
    const-string v2, "timed out"

    :log
    invoke-static {}, Landroid/os/SystemClock;->elapsedRealtime()J

    move-result-wide v5

    sub-long/2addr v5, v3

    new-instance v0, Ljava/lang/StringBuilder;

    invoke-direct {v0}, Ljava/lang/StringBuilder;-><init>()V

    const-string v1, "waited "

    invoke-virtual {v0, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    invoke-virtual {v0, v5, v6}, Ljava/lang/StringBuilder;->append(J)Ljava/lang/StringBuilder;

    const-string v1, " ms for OneOS\'s albums ("

    invoke-virtual {v0, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    invoke-virtual {v0, v2}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    const-string v1, ")"

    invoke-virtual {v0, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    invoke-virtual {v0}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;

    move-result-object v0

    const-string v1, "OneOSMediaHold"

    invoke-static {v1, v0}, Landroid/util/Slog;->i(Ljava/lang/String;Ljava/lang/String;)I
    :try_end_0
    .catch Ljava/lang/InterruptedException; {:try_start_0 .. :try_end_0} :interrupted
    .catch Ljava/lang/Throwable; {:try_start_0 .. :try_end_0} :failed

    :done
    return-void

    # Thread.sleep was interrupted: keep the flag for the handler thread's
    # owner, stop waiting and say so.
    :interrupted
    invoke-static {}, Ljava/lang/Thread;->currentThread()Ljava/lang/Thread;

    move-result-object v0

    invoke-virtual {v0}, Ljava/lang/Thread;->interrupt()V

    const-string v2, "interrupted"

    goto :log

    # Anything else: no wait, the mount goes on as before.
    :failed
    return-void
.end method
