/* Stock 24020/24021 addresses (code offset == runtime address). */
stock_dispatch_end = 0x3b2b6;
stock_adc_scan = 0x2bf78;
stock_screen = 0x7550c;
stock_screen_body = 0x75510;
stock_reply = 0x39014;
stock_ticks = 0x77bec;
stock_scheduler_state = 0x84a88;       /* xTaskGetSchedulerState: 2 running */
stock_stack_high_water = 0x84a54;      /* uxTaskGetStackHighWaterMark, words */
stock_screen_shadow = 0x2000d2f8 - 33660;
stock_battery_level = 0x2000d2f8 - 37104;
stock_usb_power = 0x2000d2f8 - 34529;
stock_charge_pin = 0x2000d2f8 - 34530;
stock_ble_connected = 0x2000d2f8 - 30420;   /* written only by the BLE callback @0x7af0c */
stock_current_task = 0x2000d2f8 - 28824;    /* pxCurrentTCB */
stock_device_task = 0x2000d2f8 - 34324;     /* DeviceService handle, created @0x31a74 */
_SDA_BASE_ = 0x2000d2f8;

SECTIONS {
    .key_hook 0x2d458 : { KEEP(*(.key_hook)) }
    .screen_hook 0x7550c : { KEEP(*(.screen_hook)) }
    .command_slot 0x39d70 : { KEEP(*(.command_slot)) }
    .text 0x1ca000 : { *(.text*) *(.rodata*) }
    .data : { *(.data*) *(.sdata*) *(.bss*) *(.sbss*) *(COMMON) }
    /DISCARD/ : { *(.comment) *(.note*) }
}
ASSERT(SIZEOF(.key_hook) == 4, "key hook size")
ASSERT(SIZEOF(.screen_hook) == 4, "screen hook size")
ASSERT(SIZEOF(.command_slot) == 4, "command slot size")
ASSERT(SIZEOF(.data) == 0, "no RAM sections: state lives at 0x2004b000")
ASSERT(ADDR(.text) + SIZEOF(.text) <= 0x1ee000, "code crosses the reserved area")
