package com.fahadali.oneos.guest

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build

/** Only counts in the reminder. No names, auth tokens, alarm, or persistent worker. */
internal object GoogleAccountNotifications {
    const val SIGN_IN_ALL = "sign_in_all"
    const val LATER = "com.fahadali.oneos.guest.ACCOUNT_REMINDER_LATER"
    private const val CHANNEL = "oneos-google-accounts"
    private const val ID = 7103

    private fun prefs(ctx: Context) = ctx.getSharedPreferences("account-reminders", Context.MODE_PRIVATE)
    private fun manager(ctx: Context) = ctx.getSystemService(NotificationManager::class.java)

    fun available(ctx: Context): Boolean =
        (Build.VERSION.SDK_INT < 33 || ctx.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED) &&
            manager(ctx)?.areNotificationsEnabled() == true

    fun enable(ctx: Context) {
        prefs(ctx).edit().putBoolean("dismissed", false).apply()
        refresh(ctx)
    }

    fun dismiss(ctx: Context) {
        prefs(ctx).edit().putBoolean("dismissed", true).apply()
        manager(ctx)?.cancel(ID)
    }

    fun refresh(ctx: Context) {
        // A missing permission or unavailable notification service must never block account import.
        runCatching {
            val nm = manager(ctx) ?: return
            val count = GoogleAccountInbox.load(ctx).size
            if (count == 0 || prefs(ctx).getBoolean("dismissed", false)) {
                nm.cancel(ID)
                return
            }
            if (!available(ctx)) return
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "Google account reminders", NotificationManager.IMPORTANCE_DEFAULT))
            val flags = PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
            val open = PendingIntent.getActivity(ctx, 7104, Intent(ctx, GoogleAccountsActivity::class.java), flags)
            val all = PendingIntent.getActivity(ctx, 7105,
                Intent(ctx, GoogleAccountsActivity::class.java).putExtra(SIGN_IN_ALL, true), flags)
            val later = PendingIntent.getBroadcast(ctx, 7106,
                Intent(ctx, GoogleAccountReminderReceiver::class.java).setAction(LATER), flags)
            val public = Notification.Builder(ctx, CHANNEL)
                .setSmallIcon(R.drawable.ic_tile)
                .setContentTitle("OneOS account reminder")
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .build()
            val note = Notification.Builder(ctx, CHANNEL)
                .setSmallIcon(R.drawable.ic_tile)
                .setContentTitle("Google accounts awaiting sign-in")
                .setContentText("$count accounts pending")
                .setContentIntent(open)
                .setDeleteIntent(later)
                .addAction(android.R.drawable.ic_menu_send, "Sign in all", all)
                .addAction(android.R.drawable.ic_menu_close_clear_cancel, "Later", later)
                .setOnlyAlertOnce(true)
                .setVisibility(Notification.VISIBILITY_PRIVATE)
                .setPublicVersion(public)
                .build()
            nm.notify(ID, note)
        }
    }
}

class GoogleAccountReminderReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            GoogleAccountNotifications.LATER -> GoogleAccountNotifications.dismiss(context)
            Intent.ACTION_BOOT_COMPLETED -> GoogleAccountNotifications.refresh(context)
        }
    }
}
