/* Patched into stock code. Each hook section replaces exactly one 4-byte
 * instruction or jump-table entry; see remote.ld for the addresses. */

.section .key_hook,"ax"
    jal key_filter

.section .screen_hook,"ax"
    j screen_filter

.section .command_slot,"a"
    .word command_entry

/* Jump-table target for opcode 0xF0, entered with the stock dispatcher frame:
 * r6 = packet, r7 = data (opcode first), r8 = transport. */
.section .text.command_entry,"ax"
.balign 4
command_entry:
    lwi $r0, [$r8 + 0x164]
    move $r1, $r7
    lhi $r2, [$r6 + 2]
    jal remote_command
    j stock_dispatch_end

.section .text.key_filter,"ax"
.balign 2
key_filter:
    smw.adm $r6, [$sp], $r6, 2
    jal stock_adc_scan
    jal remote_key
    lmw.bim $r6, [$sp], $r6, 2
    ret

/* Replaces the first instruction of the present function; runs it again
 * before continuing into the stock body. */
.section .text.screen_filter,"ax"
.balign 2
screen_filter:
    smw.adm $r6, [$sp], $r6, 2
    move $r6, $r0
    jal remote_screen_allowed
    beqz $r0, screen_blocked
    move $r0, $r6
    lmw.bim $r6, [$sp], $r6, 2
    smw.adm $r6, [$sp], $r6, 2
    j stock_screen_body
screen_blocked:
    movi $r0, 1
    lmw.bim $r6, [$sp], $r6, 2
    ret
