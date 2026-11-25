package com.fahadali.oneos.core.model

/** Local login hints only. No passwords, auth tokens, account database or cookies. */
object GoogleAccountQueue {
    const val TYPE = "com.google"
    const val MAX_BYTES = 32768
    const val MAX_ACCOUNTS = 128
    private const val HEADER = "ONEOS-GOOGLE-ACCOUNTS-1"
    private val EMAIL = Regex("[A-Za-z0-9._%+!&'=/^~-]{1,128}@[A-Za-z0-9.-]{1,253}")

    fun valid(name: String): Boolean = name.length <= 254 && EMAIL.matches(name) &&
        name.substringAfter('@').let { it.contains('.') && !it.startsWith('.') && !it.endsWith('.') && !it.contains("..") }

    fun encode(accounts: Collection<String>): ByteArray {
        require(accounts.all(::valid)) { "Invalid account address" }
        val names = accounts.distinct().sorted()
        require(names.size <= MAX_ACCOUNTS) { "Too many accounts" }
        return (listOf(HEADER) + names + "END").joinToString("\n", postfix = "\n")
            .toByteArray(Charsets.UTF_8).also { require(it.size <= MAX_BYTES) }
    }

    /** One foreground batch visits each pending address at most once. Skips remain pending. */
    fun next(pending: List<String>, attempted: Set<String>): String? =
        pending.firstOrNull { it !in attempted }

    fun confirmed(requested: String, returnedName: String?, returnedType: String?): Boolean =
        requested == returnedName && returnedType == TYPE

    fun decode(bytes: ByteArray): List<String> {
        require(bytes.size <= MAX_BYTES)
        val lines = bytes.toString(Charsets.UTF_8).split('\n')
        require(lines.size >= 3 && lines.first() == HEADER && lines.takeLast(2) == listOf("END", ""))
        val names = lines.drop(1).dropLast(2)
        require(names.size <= MAX_ACCOUNTS && names.all(::valid) && names.distinct().size == names.size)
        return names
    }
}
