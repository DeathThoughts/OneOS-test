package com.fahadali.oneos.ui

import android.content.Context
import android.util.AttributeSet
import androidx.appcompat.content.res.AppCompatResources
import androidx.appcompat.widget.AppCompatImageView
import com.fahadali.oneos.R
import com.fahadali.oneos.core.model.ImageRef
import com.fahadali.oneos.core.model.Images
import com.fahadali.oneos.core.model.SystemFamily

/** Full-bleed artwork shared by each system's catalog, home and setup screens. */
class OsArtworkView @JvmOverloads constructor(context: Context, attrs: AttributeSet? = null) : AppCompatImageView(context, attrs) {
    private var artwork = 0
    internal var artworkImageId: String? = null
        private set

    init {
        scaleType = ScaleType.CENTER_CROP
        importantForAccessibility = IMPORTANT_FOR_ACCESSIBILITY_NO
        setBackgroundResource(R.drawable.catalog_card_bg)
        foreground = AppCompatResources.getDrawable(context, R.drawable.catalog_card_outline)
        clipToOutline = true
    }

    fun bind(image: ImageRef?) {
        artworkImageId = image?.id
        val resource = when (image?.id) {
            Images.ID_FIND_N6 -> R.drawable.banner_coloros_find_n6
            Images.ID_FIND_X9U_IN, Images.ID_FIND_X9U_IN_1105 -> R.drawable.banner_coloros_find_x9u
            Images.ID_COLOROS17_TWINTY4C -> R.drawable.banner_coloros_twinity
            Images.ID_COLOROS17_PYB110 -> R.drawable.banner_coloros_pyb110
            Images.ID_HYPEROS_LHASA -> R.drawable.banner_hyperos_lhasa
            Images.ID_HYPEROS_HONGKONG -> R.drawable.banner_hyperos_hongkong
            Images.ID_HYPEROS_POPSICLE -> R.drawable.banner_hyperos_popsicle
            Images.ID_HYPEROS_MADRID -> R.drawable.banner_hyperos_madrid
            Images.ID_PIXEL_YOGI -> R.drawable.banner_pixel_yogi
            Images.ID_PIXEL_KODIAK -> R.drawable.banner_pixel_kodiak
            Images.ID_PIXEL_KOMODO -> R.drawable.banner_pixel_komodo
            Images.ID_NOTHING_METROID -> R.drawable.banner_nothing_metroid
            Images.ID_NOTHING_ASTEROIDS -> R.drawable.banner_nothing_asteroids
            Images.ID_INFINITYX -> R.drawable.banner_infinityx
            Images.ID_OXYGEN16 -> R.drawable.banner_oxygenos
            Images.ID_ORIGINOS7 -> R.drawable.banner_originos
            Images.ID_LINEAGE -> R.drawable.banner_lineage
            Images.ID_ONEUI95 -> R.drawable.banner_oneui
            else -> when (image?.family) {
                SystemFamily.HYPEROS -> R.drawable.banner_hyperos_lhasa
                SystemFamily.PIXEL -> R.drawable.banner_pixel_yogi
                SystemFamily.NOTHING -> R.drawable.banner_nothing_metroid
                SystemFamily.INFINITYX -> R.drawable.banner_infinityx
                SystemFamily.OXYGENOS -> R.drawable.banner_oxygenos
                SystemFamily.ORIGINOS -> R.drawable.banner_originos
                SystemFamily.LINEAGE -> R.drawable.banner_lineage
                SystemFamily.ONEUI -> R.drawable.banner_oneui
                else -> R.drawable.banner_coloros_find_n6
            }
        }
        if (artwork == resource) return
        artwork = resource
        setImageResource(resource)
    }
}
