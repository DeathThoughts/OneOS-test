package com.fahadali.oneos.ui

import android.content.Context
import android.content.res.ColorStateList
import android.content.res.Configuration
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.ColorDrawable
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.RippleDrawable
import android.graphics.drawable.StateListDrawable
import android.view.Gravity
import android.view.View
import android.view.Window
import android.view.WindowManager
import android.view.accessibility.AccessibilityNodeInfo
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.appcompat.app.AppCompatDialog
import androidx.core.graphics.ColorUtils
import androidx.core.view.ViewCompat
import com.fahadali.oneos.R
import com.fahadali.oneos.core.model.AndroidRelease
import com.fahadali.oneos.core.model.ImageRef
import com.fahadali.oneos.core.model.SystemFamily
import com.fahadali.oneos.core.model.Units

/** Local visual tokens only: never used to decide compatibility, readiness or launch. */
internal object RomVisuals {
    data class Palette(val accent: Int, val end: Int, val ink: Int, val muted: Int, val base: Int, val night: Boolean)
    data class Version(val image: ImageRef, val status: String, val tone: Int)

    fun dp(ctx: Context, value: Int): Int = (ctx.resources.displayMetrics.density * value + .5f).toInt()
    private fun c(hex: String) = Color.parseColor(hex)

    fun palette(ctx: Context, family: SystemFamily): Palette {
        val (a, b) = when (family) {
            SystemFamily.COLOROS -> "#6433CB" to "#A43C93"
            SystemFamily.HYPEROS -> "#184AC6" to "#087BAA"
            SystemFamily.OXYGENOS -> "#B52242" to "#D03C30"
            SystemFamily.PIXEL -> "#1A659A" to "#237B53"
            SystemFamily.PIXELOS -> "#155C9C" to "#14816C"
            SystemFamily.NOTHING -> "#46464E" to "#63636D"
            SystemFamily.ONEUI -> "#5046BC" to "#A33983"
            SystemFamily.INFINITYX -> "#254FC3" to "#5D38BF"
            SystemFamily.LINEAGE -> "#126D77" to "#087E80"
            SystemFamily.ORIGINOS -> "#99526C" to "#B65B43"
        }
        val night = ctx.resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK == Configuration.UI_MODE_NIGHT_YES
        return Palette(c(a), c(b), c(if (night) "#F6F4FA" else "#20202B"),
            c(if (night) "#C2C0CF" else "#555563"), ctx.getColor(R.color.preboot_card), night)
    }

    fun blend(base: Int, accent: Int, fraction: Float): Int = ColorUtils.blendARGB(base, accent, fraction)
    fun foreground(p: Palette, tone: Int = p.accent): Int =
        if (p.night) blend(tone, Color.WHITE, .54f) else blend(tone, Color.BLACK, .24f)

    fun surface(ctx: Context, p: Palette, tone: Int = p.accent, selected: Boolean = false, radius: Int = 20): GradientDrawable {
        val first = blend(p.base, tone, if (selected) .24f else if (p.night) .14f else .07f)
        val second = blend(p.base, p.end, if (selected) .16f else if (p.night) .04f else .02f)
        return GradientDrawable(GradientDrawable.Orientation.TL_BR, intArrayOf(first, second)).apply {
            cornerRadius = dp(ctx, radius).toFloat()
            setStroke(dp(ctx, if (selected) 2 else 1), blend(p.base, foreground(p, tone), if (selected) .75f else .18f))
        }
    }

    fun pressable(ctx: Context, p: Palette, selected: Boolean = false): RippleDrawable =
        RippleDrawable(ColorStateList.valueOf(blend(Color.TRANSPARENT, p.accent, .24f)),
            surface(ctx, p, selected = selected), null)

    fun action(button: Button, p: Palette) {
        val ctx = button.context
        val active = GradientDrawable(GradientDrawable.Orientation.LEFT_RIGHT, intArrayOf(p.accent, p.end)).apply {
            cornerRadius = dp(ctx, 28).toFloat()
            setStroke(dp(ctx, 1), blend(p.accent, Color.WHITE, .23f))
        }
        val disabled = GradientDrawable().apply {
            setColor(blend(p.accent, p.end, .5f)); cornerRadius = dp(ctx, 28).toFloat()
            alpha = 145
        }
        val states = StateListDrawable().apply {
            addState(intArrayOf(-android.R.attr.state_enabled), disabled)
            addState(intArrayOf(), active)
        }
        ViewCompat.setBackgroundTintList(button, null)
        button.background = RippleDrawable(ColorStateList.valueOf(0x33FFFFFF), states, null)
        button.setTextColor(Color.WHITE)
    }

    fun label(ctx: Context, value: CharSequence, size: Float, color: Int, bold: Boolean = false): TextView =
        TextView(ctx).apply {
            text = value; textSize = size; setTextColor(color)
            if (bold) setTypeface(typeface, Typeface.BOLD)
            includeFontPadding = false
            breakStrategy = android.text.Layout.BREAK_STRATEGY_SIMPLE
        }

    fun showPicker(ctx: Context, familyName: String, versions: List<Version>, chosen: (ImageRef) -> Unit) {
        if (versions.isEmpty()) return
        val p = palette(ctx, versions.first().image.family)
        val dialog = AppCompatDialog(ctx)
        dialog.supportRequestWindowFeature(Window.FEATURE_NO_TITLE)
        val root = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(ctx, 12), 0, dp(ctx, 12), dp(ctx, 12))
        }
        val sheet = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(ctx, 16), dp(ctx, 10), dp(ctx, 16), dp(ctx, 16))
            background = surface(ctx, p, radius = 28)
        }
        root.addView(sheet, LinearLayout.LayoutParams(-1, -2))
        sheet.addView(View(ctx).apply {
            background = GradientDrawable().apply {
                setColor(blend(p.base, p.muted, .45f)); cornerRadius = dp(ctx, 3).toFloat()
            }
            importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
        }, LinearLayout.LayoutParams(dp(ctx, 36), dp(ctx, 4)).apply {
            gravity = Gravity.CENTER_HORIZONTAL; bottomMargin = dp(ctx, 16)
        })
        sheet.addView(label(ctx, ctx.getString(R.string.catalog_choose_version, familyName), 21f, p.ink, true))
        sheet.addView(label(ctx, ctx.getString(R.string.catalog_version_count, versions.size), 13f, p.muted),
            LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(ctx, 5); bottomMargin = dp(ctx, 14) })
        val list = LinearLayout(ctx).apply { orientation = LinearLayout.VERTICAL }
        val scroll = ScrollView(ctx).apply { isFillViewport = false; addView(list); clipToPadding = false }
        // Bound the scroll area so landscape / large-font users can always reach the actions.
        val limit = (ctx.resources.configuration.screenHeightDp * .44f).toInt().coerceAtLeast(80)
        sheet.addView(scroll, LinearLayout.LayoutParams(-1, dp(ctx, minOf(limit, versions.size * 113))))
        val rows = mutableListOf<LinearLayout>()
        val checks = mutableListOf<android.widget.RadioButton>()
        var selected = 0
        fun redrawSelection() {
            rows.forEachIndexed { i, row ->
                row.isSelected = i == selected
                row.background = pressable(ctx, p, i == selected)
                checks[i].isChecked = i == selected
            }
        }
        versions.forEachIndexed { index, version ->
            val row = LinearLayout(ctx).apply {
                orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL
                minimumHeight = dp(ctx, 96)
                setPadding(dp(ctx, 10), dp(ctx, 12), dp(ctx, 8), dp(ctx, 12))
                isFocusable = true; isClickable = true
                contentDescription = listOf(version.image.displayName, version.status, Units.bytes(version.image.gzBytes)).joinToString(", ")
                accessibilityDelegate = object : View.AccessibilityDelegate() {
                    override fun onInitializeAccessibilityNodeInfo(host: View, info: AccessibilityNodeInfo) {
                        super.onInitializeAccessibilityNodeInfo(host, info)
                        info.className = android.widget.RadioButton::class.java.name
                        info.isCheckable = true; info.isChecked = index == selected
                    }
                }
            }
            val art = OsArtworkView(ctx).apply { bind(version.image) }
            row.addView(art, LinearLayout.LayoutParams(dp(ctx, 56), dp(ctx, 56)).apply { marginEnd = dp(ctx, 10) })
            val labels = LinearLayout(ctx).apply {
                orientation = LinearLayout.VERTICAL
                importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS
            }
            labels.addView(label(ctx, version.image.variant ?: version.image.name, 16f, p.ink, true))
            labels.addView(label(ctx, "Android " + AndroidRelease.name(version.image.sdk), 13f, p.muted),
                LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(ctx, 4) })
            labels.addView(label(ctx, version.status + " · " + Units.bytes(version.image.gzBytes), 12f,
                foreground(p, version.tone), true), LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(ctx, 6) })
            row.addView(labels, LinearLayout.LayoutParams(0, -2, 1f))
            val radio = android.widget.RadioButton(ctx).apply {
                buttonTintList = ColorStateList.valueOf(foreground(p))
                isClickable = false; isFocusable = false
                importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
                minimumWidth = 0; minWidth = 0; setPadding(0, 0, 0, 0)
            }
            row.addView(radio, LinearLayout.LayoutParams(-2, -2))
            rows.add(row); checks.add(radio)
            row.setOnClickListener { selected = index; redrawSelection() }
            list.addView(row, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(ctx, 8) })
        }
        val actions = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL
        }
        val cancel = androidx.appcompat.widget.AppCompatButton(ctx).apply {
            text = ctx.getString(android.R.string.cancel); isAllCaps = false; textSize = 14f
            setTextColor(p.ink); background = pressable(ctx, p)
            minimumHeight = dp(ctx, 48)
            setOnClickListener { dialog.dismiss() }
        }
        val next = androidx.appcompat.widget.AppCompatButton(ctx).apply {
            text = ctx.getString(R.string.rom_visual_continue); isAllCaps = false; textSize = 14f
            minimumHeight = dp(ctx, 48)
            action(this, p)
            setOnClickListener { val version = versions[selected].image; dialog.dismiss(); chosen(version) }
        }
        actions.addView(cancel, LinearLayout.LayoutParams(0, -2, 1f).apply { marginEnd = dp(ctx, 8) })
        actions.addView(next, LinearLayout.LayoutParams(0, -2, 1f))
        sheet.addView(actions, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(ctx, 10) })
        redrawSelection()
        dialog.setContentView(root)
        dialog.show()
        dialog.window?.apply {
            setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))
            setLayout(-1, -2)
            setGravity(Gravity.BOTTOM)
            addFlags(WindowManager.LayoutParams.FLAG_DIM_BEHIND)
            setDimAmount(.52f)
        }
    }
}
