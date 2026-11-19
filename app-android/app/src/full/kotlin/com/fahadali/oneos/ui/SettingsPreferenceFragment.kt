package com.fahadali.oneos.ui

import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import androidx.preference.ListPreference
import androidx.preference.Preference
import androidx.preference.PreferenceCategory
import androidx.preference.PreferenceFragmentCompat
import androidx.preference.SwitchPreferenceCompat
import androidx.appcompat.app.AlertDialog
import com.fahadali.oneos.BuildConfig
import com.fahadali.oneos.session.RemoveJob
import com.fahadali.oneos.R
import com.fahadali.oneos.core.model.Profiles
import com.fahadali.oneos.core.root.OneUiAlbum
import com.fahadali.oneos.session.LogJob
import com.fahadali.oneos.session.OneUiMedia
import com.fahadali.oneos.session.SessionPrefs
import dev.oneuiproject.oneui.R as OuiIcons

/** SESL9 preference rows, the same settings component used by Twidget. */
class SettingsPreferenceFragment : InsetPreferenceFragment() {
    // The rebuild refreshes rows changed elsewhere, but a new screen would put the list back at the top (the
    // owner's Galaxy S25 Ultra, 2026-11-17), so the scroll position is kept across it. setPreferenceScreen binds
    // the new adapter from a posted message, not at once, so the restore is posted behind it. listView is null
    // before the view exists (first resume): nothing to keep then.
    override fun onResume() {
        super.onResume()
        val list = listView
        val saved = list?.layoutManager?.onSaveInstanceState()
        onCreatePreferences(null, null)
        if (list != null && saved != null) list.post { list.layoutManager?.onRestoreInstanceState(saved) }
    }

    // Remove OneOS data says while it runs and how it ended (0.4.43, UX audit: no feedback at all before).
    private var unsubscribe: (() -> Unit)? = null
    private var unsubscribeMedia: (() -> Unit)? = null
    private var lastRemove: RemoveJob.Phase? = null
    // The album picker opens once per list root gave for a tap, never for one from before this screen started.
    private var shownPick = 0
    // The mode just picked, shown while root's call for it runs (see bindMedia).
    private var pendingMode: FilesMode? = null
    override fun onStart() {
        super.onStart()
        lastRemove = RemoveJob.state.phase
        unsubscribe = RemoveJob.observe { st ->
            if (!isAdded) return@observe
            val ended = lastRemove == RemoveJob.Phase.REMOVING && (st.phase == RemoveJob.Phase.DONE || st.phase == RemoveJob.Phase.FAILED)
            lastRemove = st.phase
            // Updated in place, not rebuilt: a new screen gave the list a new adapter and it jumped to the top
            // (the owner's Galaxy S25 Ultra, 2026-11-17).
            bindRemove(st)
            if (ended) st.message?.let { msg ->
                AlertDialog.Builder(requireContext()).setTitle(R.string.remove_title).setMessage(msg).setPositiveButton(android.R.string.ok, null).show()
            }
        }
        // One UI photos (build 156): root's marker folder is the only record, so it is read each time the screen opens.
        shownPick = OneUiMedia.state.pick
        unsubscribeMedia = OneUiMedia.observe { st ->
            if (!isAdded) return@observe
            bindMedia(st)  // in place, see above
            if (st.pick != shownPick) {
                shownPick = st.pick
                st.albums?.let(::showAlbumPicker)
            }
        }
        OneUiMedia.refresh(requireContext())
    }
    override fun onStop() {
        unsubscribe?.invoke(); unsubscribe = null
        unsubscribeMedia?.invoke(); unsubscribeMedia = null
        super.onStop()
    }
    override fun onCreatePreferences(state: Bundle?, rootKey: String?) {
        val ctx = preferenceManager.context
        preferenceScreen = preferenceManager.createPreferenceScreen(ctx)
        fun category(title: Int) = PreferenceCategory(ctx).apply { setTitle(title); preferenceScreen.addPreference(this) }
        fun row(group: PreferenceCategory, title: Int, summary: Int? = null, icon: Int = 0, target: Class<*>) {
            group.addDescribedPreference(Preference(ctx).apply {
                key = "setting_$title"
                setTitle(title)
                if (summary != null) setSummary(summary)
                isIconSpaceReserved = icon != 0
                if (icon != 0) setIcon(icon)
                setOnPreferenceClickListener { startActivity(Intent(ctx, target)); true }
            })
        }
        val setup = category(R.string.advanced_card_system)
        row(setup, R.string.image_title, R.string.advanced_file_summary, OuiIcons.drawable.ic_oui_folder_open, ImageActivity::class.java)
        row(setup, R.string.space_title, R.string.advanced_space_summary, OuiIcons.drawable.ic_oui_manage_storage_outline, OldStagesActivity::class.java)
        row(setup, R.string.prepare_title, R.string.advanced_setup_summary, OuiIcons.drawable.ic_oui_refresh, PrepareActivity::class.java)
        setup.addDescribedPreference(Preference(ctx).apply {
            key = "remove_data"
            setIcon(OuiIcons.drawable.ic_oui_delete_outline)
            setOnPreferenceClickListener {
                if (!RemoveJob.canStart()) android.widget.Toast.makeText(ctx, R.string.remove_busy, android.widget.Toast.LENGTH_LONG).show()
                else AlertDialog.Builder(ctx).setTitle(R.string.remove_confirm_title).setMessage(R.string.remove_confirm_message)
                    .setPositiveButton(R.string.remove_confirm_run) { _, _ -> activity?.let { SystemManage.removeAll(it) } }
                    .setNegativeButton(android.R.string.cancel, null).show()
                true
            }
        })
        fun toggle(group: PreferenceCategory, title: Int, summary: Int, value: Boolean, save: (Boolean) -> Unit) {
            group.addDescribedPreference(SwitchPreferenceCompat(ctx).apply {
                key = "setting_$title"
                setTitle(title); setSummary(summary); isIconSpaceReserved = false; isPersistent = false; isChecked = value
                setOnPreferenceChangeListener { _, next -> save(next as Boolean); true }
            })
        }
        val sessions = category(R.string.advanced_card_sessions)
        row(sessions, R.string.last_title, target = LastSessionActivity::class.java)
        toggle(sessions, R.string.advanced_heat_cap_title, R.string.advanced_heat_cap_summary, SessionPrefs.heatCap(ctx)) { SessionPrefs.setHeatCap(ctx, it) }
        toggle(sessions, R.string.guest_patch_auto_title, R.string.guest_patch_auto_summary, SessionPrefs.autoGuestPatches(ctx)) {
            SessionPrefs.setAutoGuestPatches(ctx, it)
        }
        toggle(sessions, R.string.advanced_full_logs_title, R.string.advanced_full_logs_summary, SessionPrefs.fullLogs(ctx)) { SessionPrefs.setFullLogs(ctx, it) }
        // One UI files in the other system: one choice instead of the two switches there were (the owner found two
        // confusing; owner's decision of 2026-11-18, after the S25 Ultra). It writes the two mechanisms underneath
        // as they were: view = root's oneui-media marker (read-only albums), full = SessionPrefs.shareFiles
        // (launch arg, real folders read-write). The listener returns false: the row follows the state when it
        // updates, so a refusal from root or a cancelled question never shows a wrong position.
        sessions.addDescribedPreference(ListPreference(ctx).apply {
            key = FILES_KEY
            setTitle(R.string.oneui_files_title)
            entries = arrayOf<CharSequence>(getString(R.string.oneui_files_off), getString(R.string.oneui_files_view), getString(R.string.oneui_files_full))
            entryValues = arrayOf<CharSequence>(FilesMode.OFF.key, FilesMode.VIEW.key, FilesMode.FULL.key)
            isIconSpaceReserved = false; isPersistent = false
            setOnPreferenceChangeListener { _, next ->
                val mode = FilesMode.of(next as String)
                if (mode == FilesMode.FULL) {
                    // Real files, read and write: only on a yes (owner's decision of 2026-11-18).
                    AlertDialog.Builder(requireContext()).setTitle(R.string.oneui_files_full_confirm_title)
                        .setMessage(R.string.oneui_files_full_confirm_message)
                        .setPositiveButton(R.string.oneui_files_full_confirm_run) { _, _ -> applyMode(ctx, mode) }
                        .setNegativeButton(android.R.string.cancel, null).show()
                } else applyMode(ctx, mode)
                false
            }
        })
        // Which of One UI's albums ColorOS shows (owner's decision after the Galaxy S25 Ultra, session
        // 92032e7ae892154c): root lists them on each tap, so a folder made in One UI since shows up; the
        // choice is root's file, read at the next guest boot, and is offered under "View One UI photos".
        sessions.addDescribedPreference(Preference(ctx).apply {
            key = ALBUMS_KEY
            setTitle(R.string.oneui_media_albums_title)
            isIconSpaceReserved = false
            setOnPreferenceClickListener { OneUiMedia.choose(ctx); true }
        })
        val experimental = category(R.string.settings_experimental)
        toggle(experimental, R.string.advanced_sec_ims_title, R.string.advanced_sec_ims_summary, SessionPrefs.secIms(ctx)) { SessionPrefs.setSecIms(ctx, it) }
        toggle(experimental, R.string.advanced_edm_stub_title, R.string.advanced_edm_stub_summary, SessionPrefs.edmStub(ctx)) { SessionPrefs.setEdmStub(ctx, it) }
        toggle(experimental, R.string.verbose_boot_title, R.string.verbose_boot_summary, SessionPrefs.verboseBoot(ctx)) { SessionPrefs.setVerboseBoot(ctx, it) }
        if (BuildConfig.DEBUG && listOf(Profiles.S_8550, Profiles.A55).any { Build.MODEL.startsWith(it.modelPrefix) }) {
            experimental.addDescribedPreference(SwitchPreferenceCompat(ctx).apply {
                key = "setting_guest_vulkan"
                setTitle(R.string.guest_vulkan_title)
                setSummary(R.string.guest_vulkan_summary)
                isIconSpaceReserved = false
                isPersistent = false
                isChecked = SessionPrefs.guestVulkan(ctx)
                setOnPreferenceChangeListener { _, next ->
                    if (next != true) {
                        SessionPrefs.setGuestVulkan(ctx, false)
                        return@setOnPreferenceChangeListener true
                    }
                    AlertDialog.Builder(ctx)
                        .setTitle(R.string.guest_vulkan_title)
                        .setMessage(R.string.guest_vulkan_warning)
                        .setPositiveButton(R.string.guest_vulkan_enable) { _, _ ->
                            SessionPrefs.setGuestVulkan(ctx, true)
                            isChecked = true
                        }
                        .setNegativeButton(android.R.string.cancel, null)
                        .show()
                    false
                }
            })
        }
        val risky = category(R.string.advanced_card_risky)
        // 0.5.17 (owner): turning it on asks first, in red, with a 5 s wait (LockRiskDialog); off needs no question.
        risky.addDescribedPreference(SwitchPreferenceCompat(ctx).apply {
            key = "setting_${R.string.advanced_allow_lock_title}"
            setTitle(R.string.advanced_allow_lock_title); setSummary(R.string.advanced_allow_lock_summary)
            isIconSpaceReserved = false; isPersistent = false; isChecked = SessionPrefs.allowScreenLock(ctx)
            setOnPreferenceChangeListener { pref, next ->
                if (next != true) { SessionPrefs.setAllowScreenLock(ctx, false); return@setOnPreferenceChangeListener true }
                LockRiskDialog.show(requireActivity()) { yes ->
                    if (yes) { SessionPrefs.setAllowScreenLock(ctx, true); (pref as SwitchPreferenceCompat).isChecked = true }
                }
                false
            }
        })
        LogJob.latestZip(ctx)?.let { zip ->
            sessions.addDescribedPreference(Preference(ctx).apply {
                key = "session_logs"
                setTitle(R.string.logs_title)
                setSummary(R.string.advanced_logs_summary)
                isIconSpaceReserved = false
                setOnPreferenceClickListener {
                    startActivity(Intent(ctx, FindingsActivity::class.java).putExtra(FindingsActivity.EXTRA_ZIP, zip.path)); true
                }
            })
        }
        if (SessionRestore.offered(ctx)) {
            sessions.addDescribedPreference(Preference(ctx).apply {
                setTitle(R.string.last_restore_title); isIconSpaceReserved = false
                setOnPreferenceClickListener { activity?.let { SessionRestore.confirm(it) }; true }
            })
        }
        val help = category(R.string.settings_help)
        row(help, R.string.about_title, target = AboutActivity::class.java)
        row(help, R.string.team_title, R.string.advanced_team_summary, target = DebugSettingsActivity::class.java)
        preferenceScreen.addBottomInset()
        // Initial values come from the same functions the observers use, so the two never disagree.
        bindMedia(OneUiMedia.state)
        bindRemove(RemoveJob.state)
    }

    // State updates change the existing rows and never rebuild the screen: a rebuild gives the RecyclerView a
    // new adapter and the list jumps to the top (the owner's Galaxy S25 Ultra, 2026-11-17). Preference.setValue
    // does not call the change listener (only a user pick does), so this cannot re-trigger applyMode.
    private fun bindMedia(st: OneUiMedia.State) {
        // While root's call runs its state still says the old thing, so the pick is shown until it answered.
        if (!st.busy) pendingMode = null
        val mode = pendingMode ?: currentMode(requireContext(), st)
        findPreference<ListPreference>(FILES_KEY)?.apply {
            value = mode.key; summary = filesSummary(st, mode); isEnabled = !st.busy
        }
        findPreference<Preference>(ALBUMS_KEY)?.apply {
            summary = albumsSummary(st); isEnabled = !st.busy && mode == FilesMode.VIEW
        }
    }

    /** What the setting is now: full if Share my files is on, else view if root's marker is on, else off. */
    private fun currentMode(ctx: Context, st: OneUiMedia.State): FilesMode = when {
        SessionPrefs.shareFiles(ctx) -> FilesMode.FULL
        st.media?.on == true -> FilesMode.VIEW
        else -> FilesMode.OFF
    }

    /** Writes [mode] into the two mechanisms underneath; root's call is asynchronous, the flag is not. */
    private fun applyMode(ctx: Context, mode: FilesMode) {
        if (OneUiMedia.state.busy) return  // OneUiMedia would drop the call: change nothing
        SessionPrefs.setShareFiles(ctx, mode == FilesMode.FULL)
        val albumsOn = mode == FilesMode.VIEW
        if (OneUiMedia.state.media?.on != albumsOn) {
            pendingMode = mode
            OneUiMedia.set(ctx, albumsOn)
        }
        bindMedia(OneUiMedia.state)
    }

    private enum class FilesMode(val key: String) {
        OFF("off"), VIEW("view"), FULL("full");
        companion object { fun of(key: String) = values().firstOrNull { it.key == key } ?: OFF }
    }

    private fun bindRemove(rs: RemoveJob.State) {
        findPreference<Preference>("remove_data")?.apply {
            setTitle(if (rs.busy) R.string.remove_running else R.string.remove_title)
            if (!rs.busy && rs.message != null) summary = rs.message else setSummary(R.string.remove_summary)
            isEnabled = !rs.busy
        }
    }

    private companion object {
        val FILES_KEY = "setting_${R.string.oneui_files_title}"
        val ALBUMS_KEY = "setting_${R.string.oneui_media_albums_title}"
    }

    /** The chosen mode's text, then what root said when it read or changed nothing, or what is at the marker's path. */
    private fun filesSummary(st: OneUiMedia.State, mode: FilesMode): String {
        val base = getString(when (mode) {
            FilesMode.OFF -> R.string.oneui_files_off_summary
            FilesMode.VIEW -> R.string.oneui_files_view_summary
            FilesMode.FULL -> R.string.oneui_files_full_summary
        })
        val problem = st.problem.takeUnless { st.problemOnAlbums }
        val why = st.media?.why
        val note = when {
            st.busy -> getString(R.string.oneui_media_asking)
            problem != null -> getString(if (st.problemOnRead) R.string.oneui_media_unread else R.string.oneui_media_problem, problem)
            why != null -> getString(R.string.oneui_media_not_marker, why)
            else -> return base
        }
        return "$base\n\n$note"
    }

    /** "N of M albums shown" once root listed them ("All albums" before), then what went wrong, as root said it. */
    private fun albumsSummary(st: OneUiMedia.State): String {
        val albums = st.albums
        val base = if (albums == null) getString(R.string.oneui_media_albums_all)
        else getString(R.string.oneui_media_albums_count, albums.count { !it.excluded }, albums.size)
        val problem = st.problem.takeIf { st.problemOnAlbums }
        val why = st.exclusionsWhy
        val notes = listOfNotNull(
            if (st.busy) getString(R.string.oneui_media_asking) else null,
            problem?.let { getString(if (st.problemOnRead) R.string.oneui_media_unread else R.string.oneui_media_problem, it) },
            why?.let { getString(R.string.oneui_media_albums_bad_list, it) },
            st.albumsLeftOut.takeIf { it > 0 }?.let { getString(R.string.oneui_media_albums_left_out, it) },
        )
        return if (notes.isEmpty()) base else base + "\n\n" + notes.joinToString("\n")
    }

    /** One row per album, ticked when ColorOS shows it; Save sends the unticked ones to root. */
    private fun showAlbumPicker(albums: List<OneUiAlbum>) {
        val ctx = requireContext()
        if (albums.isEmpty()) {
            AlertDialog.Builder(ctx).setTitle(R.string.oneui_media_albums_title).setMessage(R.string.oneui_media_albums_none)
                .setPositiveButton(android.R.string.ok, null).show()
            return
        }
        val top = getString(R.string.oneui_media_album_top)
        val labels = Array<CharSequence>(albums.size) { i ->
            val a = albums[i]
            getString(R.string.oneui_media_album_row, a.leaf, a.parent.ifEmpty { top }, a.files)
        }
        val shown = BooleanArray(albums.size) { !albums[it].excluded }
        AlertDialog.Builder(ctx).setTitle(R.string.oneui_media_albums_title)
            .setMultiChoiceItems(labels, shown) { _, which, checked -> shown[which] = checked }
            .setPositiveButton(R.string.oneui_media_albums_save) { _, _ ->
                OneUiMedia.saveExcluded(ctx.applicationContext, albums.filterIndexed { i, _ -> !shown[i] }.map { it.id })
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }
}
