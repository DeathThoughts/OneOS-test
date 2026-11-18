package com.fahadali.oneos.guest

import android.app.Activity
import androidx.appcompat.app.AlertDialog
import java.io.File
import java.io.FileOutputStream

/** Authenticated by the same private app-data boundary as the existing return request. */
object GuestRestart {
    fun confirm(activity: Activity) {
        AlertDialog.Builder(activity).setTitle(R.string.restart_confirm_title)
            .setMessage(R.string.restart_confirm_message)
            .setNegativeButton(android.R.string.cancel,null).setPositiveButton(R.string.restart_action) { _,_ ->
                activity.startActivity(android.content.Intent(activity,ReturnSplashActivity::class.java).putExtra(EXTRA_RESTART,true).putExtra(ReturnSplashActivity.EXTRA_ASKED_AT,System.currentTimeMillis()))
            }.show()
    }
    const val EXTRA_RESTART="restart_guest"
    fun pending(context:android.content.Context)=File(context.filesDir,"oneos_restart").isFile
    fun send(context:android.content.Context) {
        val tmp=File(context.filesDir,"oneos_restart.tmp")
        FileOutputStream(tmp).use { out -> out.write("restart ${System.currentTimeMillis()/1000}\n".toByteArray());out.fd.sync() }
        if(!tmp.renameTo(File(context.filesDir,"oneos_restart"))) { tmp.delete();throw java.io.IOException("Could not request guest restart") }
    }
}
