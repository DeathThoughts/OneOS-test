package com.fahadali.oneos.guest;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.net.Uri;
import android.os.Binder;
import android.os.Bundle;
import android.util.AtomicFile;
import android.util.Log;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.util.ArrayList;

/** A native switch tile consumed by LineageOS 24's SettingsLib Sound dashboard. */
public final class DolbySoundSettingsProvider extends ContentProvider {
    private static final String TAG = "OneOSDolbySettings";
    private static final String SETTINGS = "com.android.settings";
    private static final String KEY = "oneos_dolby_atmos";
    private static final String KEY_HINT = "com.android.settings.keyhint";
    private static final String CHECKED = "checked_state";
    private static final String AUTHORITY = "content://com.fahadali.oneos.guest.dolby";
    private AtomicFile preference;

    @Override
    public boolean onCreate() {
        Context context = getContext();
        if (context == null) return false;
        preference = new AtomicFile(new File(context.getFilesDir(), "oneos_dolby_enabled"));
        return true;
    }

    @Override
    public synchronized Bundle call(String method, String arg, Bundle extras) {
        Context context = getContext();
        if (context == null || !isSystemSettings(context)) return null;

        String key = extras == null ? null : extras.getString(KEY_HINT);
        if (key != null && !KEY.equals(key)) return null;
        Bundle result = new Bundle();
        switch (method) {
            case "getEntryData":
            case "getSwitchData":
                Bundle tile = tile(context);
                if (key != null) return tile;
                ArrayList<Bundle> tiles = new ArrayList<>(1);
                tiles.add(tile);
                result.putParcelableArrayList(
                        method.equals("getEntryData") ? "entry_data" : "switch_data", tiles);
                return result;
            case "isChecked":
                if (key == null) return null;
                result.putBoolean(CHECKED, isEnabled());
                return result;
            case "onCheckedChanged":
                if (key == null || !extras.containsKey(CHECKED)) return null;
                boolean success = save(extras.getBoolean(CHECKED));
                result.putBoolean("set_checked_error", !success);
                if (!success) {
                    result.putString("set_checked_error_message",
                            context.getString(R.string.dolby_sound_settings_error));
                }
                return result;
            default:
                return null;
        }
    }

    private boolean isSystemSettings(Context context) {
        if (!SETTINGS.equals(getCallingPackage())) return false;
        try {
            ApplicationInfo info = context.getPackageManager().getApplicationInfo(SETTINGS, 0);
            return info.uid == Binder.getCallingUid()
                    && (info.flags & ApplicationInfo.FLAG_SYSTEM) != 0;
        } catch (PackageManager.NameNotFoundException e) {
            return false;
        }
    }

    private static Bundle tile(Context context) {
        Bundle tile = new Bundle();
        tile.putString("com.android.settings.category", "com.android.settings.category.ia.sound");
        tile.putString(KEY_HINT, KEY);
        tile.putString("com.android.settings.title",
                context.getString(R.string.dolby_sound_settings_title));
        tile.putString("com.android.settings.summary",
                context.getString(R.string.dolby_sound_settings_summary));
        tile.putString("com.android.settings.switch_uri", AUTHORITY);
        return tile;
    }

    private boolean isEnabled() {
        try (var input = preference.openRead()) {
            return input.read() != '0' || input.read() != -1;
        } catch (IOException e) {
            return true;
        }
    }

    private boolean save(boolean enabled) {
        FileOutputStream stream = null;
        try {
            stream = preference.startWrite();
            stream.write(enabled ? '1' : '0');
            preference.finishWrite(stream);
            return isEnabled() == enabled;
        } catch (IOException e) {
            preference.failWrite(stream);
            Log.w(TAG, "Unable to save the guest Dolby preference", e);
            return false;
        }
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection,
            String[] selectionArgs, String sortOrder) {
        throw new UnsupportedOperationException();
    }

    @Override
    public String getType(Uri uri) {
        return null;
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) {
        throw new UnsupportedOperationException();
    }

    @Override
    public int delete(Uri uri, String selection, String[] selectionArgs) {
        throw new UnsupportedOperationException();
    }

    @Override
    public int update(Uri uri, ContentValues values, String selection, String[] selectionArgs) {
        throw new UnsupportedOperationException();
    }
}
