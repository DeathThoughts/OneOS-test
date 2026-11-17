# Known dead ends (do not plan these again)

Moved out of CLAUDE.md (owner's decision, 2026-11-17) to keep the rules short;
CLAUDE.md's "Research before planning a fix" says to read this file before
planning and to add to it whenever a new dead end is found.

- ColorOS ORMS / screen-on CPU boost: ScreenOnCpuBoostHelper tries "orms core
  service" (`Orms_ManagerInner: cannot connect to orms core service!`), then
  UAH `urcc` (`Urcc is not declared`). Both are OPPO vendor-side; Samsung has
  neither (session 25b4785c4206828f). Any boost must come from OneOS's side
  (kernel nodes or Samsung's HYPER-HAL), measured first.
- Other OPPO vendor HALs missing on Samsung, same reason:
  `vendor.oplus.hardware.performance.IPerformance` (audioserver retries every
  5 s, AudioBoost rt boost ret -1), `cryptoeng.ICryptoeng` (system_server main
  thread waits 5 s at boot), `touch.IOplusTouch`, `displaypanelfeature`,
  `mmdisplayfeature`, `rpmh`, `charger`.
- CPU held at about 2.4 GHz all session: One UI's power saving
  (`settings global low_power=1`) sets `cpufreq_max_limit`; the heat cap
  respects it on purpose. Owner's setting, not a bug. One UI's framework
  stops for the guest, but the limit it wrote stays in the kernel; with power
  saving off the heat cap clears One UI's leftover limit and the guest runs at
  full clock (session 4d2e95c67f065941: 2246400 -> -1, 3.53/4.47 GHz).
- Samsung's lazy artd started by init during a guest session aborts with
  `Got callback on service which we did not register: ...IArtd`; the guest's
  own artd serves the compiles. Seen in every session, harmless.
- ColorOS face unlock that needs a swipe: the guest's
  `oplus_customize_face_unlock_remain_unlock_switch` (stay on lock screen) is
  on. Owner's setting.
- Face match time (0.8-2 s): Samsung's face HAL loads its TA on every
  authenticate (about 0.35 s) and needs several frames in low light. Inside
  Samsung's HAL; not touched.
- ColorOS Wi-Fi driver commands: OplusWifiHalService logs `Can't call
  executeDriverCommandWithResult: mIOplusWifiHalService is null` about 240
  times a minute from system_server. It needs OPPO's vendor Wi-Fi HAL, which
  Samsung does not have (ColorOS 17 audit on the Galaxy S25 Ultra,
  2026-11-16, log1-log11).
- OPPO additions to ART: `VMRuntime.SupressionGC`,
  `VMRuntime.updateProcessValue` and `setOplusAppLaunchOptFlags` are missing
  ("export/Mainline has no Oplus natives"), one line per process start. They
  come from the image's own export ART module (inferred: the same on a real
  IN Find X9 Ultra); not Samsung's or OneOS's (same audit).
- OplusCpuLimitManager: `/dev/cpuset/l-background`, `h-background` and
  `kswapd-like` fail with ENOENT. These are OPPO kernel cpusets; do not
  create them on One UI's cgroups (same audit).
- OplusStorageManagerFeature `writeF2fsRelatedNode` EACCES (about 54 traces a
  minute): ColorOS retuning Samsung's f2fs. Letting it through would change
  One UI's storage behaviour, so it stays denied (same audit).
- libprocessgroup at boot: "Unknown profile action: SetClamps", "unknown
  attribute CfqWeight/BfqWeight/GpisSfCpuset", "JoinCgroup: controller
  schedtune is not found". The guest's libprocessgroup reads Samsung's vendor
  task profiles; a few lines at boot, probably harmless (inferred; same
  audit).
- `minksocket: MinkIPC ERROR ... Client down` bursts at the framework stop,
  the return and face unlock: a side effect of killing the processes that
  held those clients; harmless (same audit).
- HYPER-HAL `set_uclamp_min(): sched_setattr failed ... err = -1` (70 of 255
  requests): inside Samsung's HAL; harmless (inferred; same audit).
- ColorOS `DynamicFramerate ... LP_SUPPORT ... not enabled`: from the feature
  `oplus.software.display.adfr_v32_lp` (OPPO ADFR), checked in
  DynamicFrameRateManagerImpl. It only sends SurfaceControl frame-rate votes,
  which cannot go below the guest's 120 Hz refresh pin (oneos_switch.sh, set
  because SurfaceFlinger thrashed 10-120 Hz during the fingerprint wave)
  (same audit, from the image's jars).
- `cpufreq_max_limit` 1958400 appearing mid-session: only while Samsung's
  camera runs (log9 05:57:25, log11 07:30:29 and 07:31:09; 3 of 3, never
  without the camera). It is Samsung camera's own CPU limit, the same as in
  One UI; the heat cap lifts it at its next level 0. Samsung's design, not a
  bug (same audit).
