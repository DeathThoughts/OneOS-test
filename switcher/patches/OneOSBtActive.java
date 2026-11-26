import java.lang.reflect.Method;
import java.util.Collection;
import java.util.List;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothProfile;
import android.os.Looper;

/** One-shot root helper: select the one connected classic audio device, never a null device.
 * Invoked only after a fresh A2DP connection and the exact Oplus active-device failure.
 * The caller checks the A2DP profile dump; no raw Binder transaction IDs or pairing changes.
 */
public final class OneOSBtActive {
    private static boolean hasActive(Method get, BluetoothAdapter adapter, int profile) throws Exception {
        Object value = get.invoke(adapter, profile);
        if (!(value instanceof List<?>)) throw new IllegalStateException("unexpected active-device reply");
        for (Object device : (List<?>) value) if (device != null) return true;
        return false;
    }

    private static void report(String message) {
        android.util.Log.i("OneOSBtActive", message);
        System.out.println(message);
    }

    // app_process does not run the ActivityThread application bootstrap.
    private static void initBluetooth() throws Exception {
        Class<?> initializer = Class.forName("android.bluetooth.BluetoothFrameworkInitializer");
        if (initializer.getMethod("getBluetoothServiceManager").invoke(null) == null) {
            Class<?> manager = Class.forName("android.os.BluetoothServiceManager");
            initializer.getMethod("setBluetoothServiceManager", manager)
                    .invoke(null, manager.getConstructor().newInstance());
        }
        report("Bluetooth framework initialized");
    }

    public static void main(String[] args) {
        try {
            // BluetoothAdapter constructs a Handler bound to the process main looper.
            // Keep it dispatching callbacks while the worker makes synchronous Binder calls.
            if (Looper.getMainLooper() == null) Looper.prepareMainLooper();
            report("Main looper initialized (v3)");
            Thread worker = new Thread(() -> {
                try {
                    System.exit(run(args));
                } catch (Throwable e) {
                    fail(e);
                }
            }, "oneos-bt-active");
            worker.start();
            Looper.loop();
            System.exit(2);
        } catch (Throwable e) {
            fail(e);
        }
    }

    private static void fail(Throwable e) {
        android.util.Log.e("OneOSBtActive", "Recovery failed", e);
        System.err.println(android.util.Log.getStackTraceString(e));
        System.exit(2);
    }

    private static int run(String[] args) throws Exception {
        if (args.length != 1 || !args[0].matches("(?i)[0-9a-f]{2}:[0-9a-f]{2}")) return 2;
        initBluetooth();
        BluetoothAdapter adapter = BluetoothAdapter.getDefaultAdapter();
        if (adapter == null || !adapter.isEnabled()) {
            report("Bluetooth adapter missing or disabled");
            return 2;
        }
        report("Bluetooth adapter initialized and enabled");
        report("Reading active Bluetooth routes");
        Method get = BluetoothAdapter.class.getMethod("getActiveDevices", int.class);
        // Do not replace any active media/assistive route.
        if (hasActive(get, adapter, BluetoothProfile.A2DP)
                || hasActive(get, adapter, 21) || hasActive(get, adapter, 22)) {
            report("already active or alternate Bluetooth audio route");
            return 0;
        }
        Method isConnected = BluetoothDevice.class.getMethod("isConnected");
        BluetoothDevice candidate = null;
        report("Reading paired devices");
        Collection<BluetoothDevice> bonded = adapter.getBondedDevices();
        for (BluetoothDevice device : bonded) {
            if (!Boolean.TRUE.equals(isConnected.invoke(device))) continue;
            if (!device.getAddress().toUpperCase(java.util.Locale.ROOT)
                    .endsWith(args[0].toUpperCase(java.util.Locale.ROOT))) continue;
            if (candidate != null) {
                report("ambiguous device suffix; no change");
                return 3;
            }
            candidate = device;
        }
        if (candidate == null || adapter.getProfileConnectionState(BluetoothProfile.A2DP)
                != BluetoothProfile.STATE_CONNECTED) {
            report("no matching connected A2DP candidate");
            return 3;
        }
        if (hasActive(get, adapter, BluetoothProfile.A2DP)) return 0;
        Method set = BluetoothAdapter.class.getMethod("setActiveDevice", BluetoothDevice.class, int.class);
        int audio = BluetoothAdapter.class.getField("ACTIVE_DEVICE_AUDIO").getInt(null);
        report("Requesting A2DP active device");
        boolean accepted = Boolean.TRUE.equals(set.invoke(adapter, candidate, audio));
        if (!accepted) {
            report("setActiveDevice returned false");
            return 4;
        }
        for (int i = 0; i < 10; i++) {
            Thread.sleep(200);
            List<?> devices = (List<?>) get.invoke(adapter, BluetoothProfile.A2DP);
            if (devices.contains(candidate)) {
                report("A2DP active device confirmed");
                return 0;
            }
        }
        report("selection accepted but active device not confirmed");
        return 5;
    }
}
