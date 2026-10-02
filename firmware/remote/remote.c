/* Remote mode for the Ditoo Pro (family 24, based on 24021-unlock).
 *
 * New BLE opcode 0xF0 with two independent features:
 *
 * Drawing mode: the stock screen output is blocked, the host draws a 16x16
 * RGB frame, and the front keys are captured (hidden from the stock UI).
 *
 * Key listening: the front keys are captured in every menu (hidden from the
 * stock UI) while the stock screen stays as it is.
 *
 * Captured keys are pushed to the host as they happen (sub-command 0x80, see
 * below). When a push is not possible the event goes to a queue instead, and
 * every 0xF0 reply carries the queued events, so nothing is lost; it only
 * arrives later.
 *
 * While keys are captured a missing host would leave the device without
 * working keys, so both modes switch themselves off:
 *   - when the BLE link drops (the stock BLE callback clears a flag),
 *   - when no 0xF0 command arrives for WATCHDOG_MS (host hung, link still up),
 *   - when any key is held for ESCAPE_MS.
 *
 * State lives in 4 KiB of RAM taken from the top of the stock heap and is only
 * trusted when the magic word matches, so a cold boot starts with everything
 * off; after a warm reboot the tick counter restarts and the watchdog ends
 * capturing.
 *
 * Tasks: 0xF0 commands run in the stock command dispatcher task, which is also
 * where frames are presented, so the frame buffer has a single writer. The key
 * hook runs in the DeviceService task (key scan loop, 1536-byte stack); it is
 * the only writer of the queue head and the push counters, the command task the
 * only reader of the queue tail. Nothing here sleeps on purpose: the stock
 * delay asserts if the scheduler is suspended. A push may block briefly on the
 * mutex of the stock reply queue, so it is only done when the scheduler runs
 * and interrupts are enabled. */

#define MAGIC 0x334d5452u        /* layout 3 (push, link check) */
#define KEY_COUNT 11
#define QUEUE_SIZE 32
#define REPLY_KEYS 24
#define WATCHDOG_MS 60000
#define ESCAPE_MS 4000
#define FRAME_BYTES 768
#define PUSH_MIN_STACK 768       /* free stack bytes needed at the hook to push */
#define SUB_PUSH 0x80

extern unsigned stock_ticks(void);
extern unsigned stock_reply(unsigned context, unsigned opcode, const void *data, unsigned size);
extern unsigned stock_screen(const void *frame);
extern unsigned stock_scheduler_state(void);             /* 2 = running */
extern unsigned stock_stack_high_water(void *task);      /* free words never used */
extern unsigned char *stock_screen_shadow;
extern volatile unsigned stock_battery_level;        /* 0..7, debounced by the stock power task */
extern volatile unsigned char stock_usb_power;       /* 1 while external power is connected */
extern volatile unsigned char stock_charge_pin;      /* 1 while charging; 0 with power = full */
extern volatile unsigned char stock_ble_connected;   /* 1 between BLE_STACK_CONNECTED and _DISCONNECTED */
extern void *volatile stock_current_task;            /* FreeRTOS pxCurrentTCB */
extern void *volatile stock_device_task;             /* DeviceService task handle */

#define TCB_STACK_START 0x30     /* pxStack: lowest address of the task stack */

struct state {
    volatile unsigned magic, active, listen, last_command;
    volatile unsigned key_read, key_write, dropped, suppressed;
    volatile unsigned context;       /* stock reply context of the last 0xF0 command */
    volatile unsigned over_ble;      /* last 0xF0 command came while BLE was connected */
    volatile unsigned pushed, low_stack, push_failed, min_free, blocked;
    unsigned held_since[KEY_COUNT];
    unsigned char keys[QUEUE_SIZE][2];
    unsigned char frame[FRAME_BYTES];
};

typedef char state_fits_reserved_ram[sizeof(struct state) <= 4096 ? 1 : -1];

#ifndef HOST_TEST
#define S ((struct state *)0x2004b000)
static inline unsigned cpu_sp(void) {
    unsigned sp;
    __asm__ volatile ("move %0, $sp" : "=r"(sp));
    return sp;
}
static inline unsigned cpu_interrupts_on(void) {   /* PSW.GIE */
    unsigned psw;
    __asm__ volatile ("mfsr %0, $psw" : "=r"(psw));
    return psw & 1;
}
#else                            /* tests/host_test.c provides these */
extern struct state *S;
unsigned cpu_sp(void);
unsigned cpu_interrupts_on(void);
#endif

static void fill(unsigned char *p, unsigned char v, unsigned n) { while (n--) *p++ = v; }

/* A key already held when capturing starts must not count as held for 4 s. */
static void reset_held(unsigned now) {
    for (unsigned i = 0; i < KEY_COUNT; ++i) S->held_since[i] = now;
}

static void clear_queue(void) {
    S->key_read = S->key_write = 0;
    S->dropped = 0;
}

/* Start from a clean state when the RAM does not hold ours yet. */
static void ensure_state(unsigned now) {
    if (S->magic == MAGIC) return;
    S->active = S->listen = 0;
    S->suppressed = 0;
    clear_queue();
    reset_held(now);
    S->last_command = now;
    S->context = 0;
    S->over_ble = 0;
    S->pushed = S->low_stack = S->push_failed = S->blocked = 0;
    S->min_free = 0xffffffffu;
    __asm__ volatile ("" ::: "memory");
    S->magic = MAGIC;
}

static void stop_remote(void) {
    S->active = 0;
    /* The stock renderer only presents frames that differ from its last one.
     * Change its copy so the next stock frame replaces ours. */
    unsigned char *shadow = stock_screen_shadow;
    if (shadow) shadow[4] ^= 0xff;
}

static void stop_capturing(void) {
    if (S->active) stop_remote();
    S->listen = 0;
}

/* Called from the key scan and the stock screen path, so it runs even when the
 * host has disappeared. The link check only applies to a host that talks over
 * BLE; the flag is read, never written. */
static void watchdog(unsigned now) {
    if (S->magic != MAGIC || !(S->active || S->listen)) return;
    if ((S->over_ble && !stock_ble_connected) || now - S->last_command > WATCHDOG_MS)
        stop_capturing();
}

static unsigned remote_on(unsigned now) {
    watchdog(now);
    return S->magic == MAGIC && S->active;
}

static unsigned mode_bits(void) {
    return S->magic != MAGIC ? 0 : (S->active ? 1 : 0) | (S->listen ? 2 : 0);
}

/* Free bytes left on the running task's stack, or 0 when the hook is not
 * running in DeviceService (then nothing is pushed). */
static unsigned stack_free(void) {
    unsigned char *tcb = stock_current_task;
    if (!tcb || tcb != (unsigned char *)stock_device_task) return 0;
    unsigned start = *(unsigned *)(tcb + TCB_STACK_START), sp = cpu_sp();
    return sp > start ? sp - start : 0;
}

/* Send a push frame from the key hook. Returns 1 when the stock reply queue
 * took it. Only called from the DeviceService task. */
static unsigned push(unsigned key, unsigned event, unsigned count) {
    if (!cpu_interrupts_on() || stock_scheduler_state() != 2 || !stock_ble_connected) {
        ++S->blocked;
        return 0;
    }
    unsigned free = stack_free();
    if (free < S->min_free) S->min_free = free;
    if (free < PUSH_MIN_STACK) {
        ++S->low_stack;
        return 0;
    }
    unsigned char msg[7] = { SUB_PUSH, 0, mode_bits(),
                             S->dropped > 255 ? 255 : S->dropped, count, key, event };
    if (!stock_reply(S->context, 0xf0, msg, 5 + 2 * count)) {
        ++S->push_failed;
        return 0;
    }
    ++S->pushed;
    return 1;
}

static void enqueue(unsigned key, unsigned event) {
    unsigned w = S->key_write;
    if (w - S->key_read < QUEUE_SIZE) {
        S->keys[w % QUEUE_SIZE][0] = key;
        S->keys[w % QUEUE_SIZE][1] = event;
        __asm__ volatile ("" ::: "memory");
        S->key_write = w + 1;
    } else ++S->dropped;
}

/* Result of the ADC key scan: key | event << 16. Key 0xff means no event.
 * Events: 1 down, 2 up, 3 long, 4 repeat, 5 release after long.
 * Returning 0xff hides the event from the stock UI. */
unsigned remote_key(unsigned packed) {
    unsigned now = stock_ticks(), key = packed & 0xffff, event = packed >> 16;
    watchdog(now);
    if (S->magic != MAGIC || key >= KEY_COUNT || event < 1 || event > 5) return packed;
    unsigned mask = 1u << key;
    if (S->suppressed & mask) {          /* rest of a press that ended capturing */
        if (event == 2 || event == 5) S->suppressed &= ~mask;
        return 0xff;
    }
    if (!S->active && !S->listen) return packed;
    if (event == 1) S->held_since[key] = now;
    if ((event == 3 || event == 4) && now - S->held_since[key] >= ESCAPE_MS) {
        S->suppressed |= mask;
        stop_capturing();
        push(0, 0, 0);                   /* tell the host now; else it sees it on the next reply */
        return 0xff;
    }
    /* Events already queued go first, so the host sees them in order. */
    if (S->key_read != S->key_write || !push(key, event, 1)) enqueue(key, event);
    return 0xff;
}

unsigned remote_screen_allowed(const void *frame) {
    return frame == S->frame || !remote_on(stock_ticks());
}

/* The stock present function copies the frame into a 4-slot ring and returns
 * 2 only while another caller is inside it. Stock callers are stopped in
 * screen_filter before that point, so one retry is enough. */
static void present(void) {
    if (stock_screen(S->frame) == 2) stock_screen(S->frame);
}

static unsigned char *put16(unsigned char *p, unsigned v) {
    if (v > 0xffff) v = 0xffff;
    p[0] = v;
    p[1] = v >> 8;
    return p + 2;
}

/* Commands (data[0] = 0xF0, data[1] = sub-command):
 *   00                         poll
 *   01                         enter drawing mode, black screen
 *   02                         leave drawing mode
 *   03 flags row count rgb...  write count rows (48 bytes each) from row;
 *                              flags bit 0 presents afterwards
 *   04 n pal[3n] pix[128]      full frame, 4-bit palette indexes, high
 *                              nibble first, n <= 16; presents
 *   05 r g b                   fill and present
 *   06                         battery; works outside the modes, changes nothing
 *   07 on                      key listening on (1) or off (0)
 *   09                         push/stack statistics; changes nothing
 * Reply (opcode 0xF0): sub status mode dropped count {key event}*count
 * mode: bit 0 drawing, bit 1 key listening.
 * Key numbers (checked on the device 2026-09-29): 0 M, 1 +, 2 left, 3 right,
 * 4 arm, 7 brightness, 9 -, 10 little button. 5, 6 and 8 not seen.
 * status: 0 ok, 1 bad length, 2 not in drawing mode, 3 unknown sub-command.
 * Sub-command 06 appends: level (0..7) external_power charging.
 * Sub-command 09 appends, each u16 little endian and capped at 0xffff:
 *   pushed, low_stack (not pushed: stack too small), push_failed (stock reply
 *   queue refused), min_free (smallest free stack seen at a push attempt,
 *   bytes; 0xffff before the first), high_water (DeviceService stack never
 *   used since boot, bytes; 0xffff if unknown), blocked (not pushed:
 *   interrupts off, scheduler not running or BLE down).
 *
 * Push (opcode 0xF0, sent by the device without a command, only while keys
 * are captured): 80 00 mode dropped count {key event}*count. Same layout as a
 * reply with sub 0x80; count is 1 for a key event and 0 when the 4 s escape
 * ended capturing (mode then shows what is still on). Sub 0x80 is never a
 * reply, so a host can tell pushes from replies by the first byte. */
void remote_command(unsigned context, const unsigned char *data, unsigned length) {
    unsigned char reply[5 + 2 * REPLY_KEYS + 12];
    /* length normally counts two trailing checksum bytes; the checks below only
     * require at least the bytes the header fields describe. */
    unsigned n = length;
    unsigned now = stock_ticks(), sub = n >= 2 ? data[1] : 0xff, status = 0;
    unsigned on = remote_on(now), battery = 0, stats = 0;

    if (n < 2) status = 1;
    else if (sub == 0) {
    } else if (sub == 1) {
        ensure_state(now);
        S->active = 0;
        if (!S->listen) clear_queue();   /* keep events a listener has not fetched */
        S->suppressed = 0;
        reset_held(now);
        fill(S->frame, 0, FRAME_BYTES);
        S->last_command = now;
        __asm__ volatile ("" ::: "memory");
        S->active = on = 1;
        present();
    } else if (sub == 2) {
        if (on) stop_remote();
        on = 0;
    } else if (sub == 6) {
        battery = 1;
    } else if (sub == 7) {
        if (n < 3) status = 1;
        else {
            unsigned want = data[2] & 1;
            ensure_state(now);
            if (want && !S->listen && !S->active) clear_queue();
            S->last_command = now;
            if (want && !S->listen) reset_held(now);
            S->listen = want;
        }
    } else if (sub == 9) {
        ensure_state(now);
        stats = 1;
    } else if (!on) status = 2;
    else if (sub == 3) {
        unsigned row = n >= 5 ? data[3] : 16, count = n >= 5 ? data[4] : 0;
        if (n < 5 || row + count > 16 || n < 5 + count * 48) status = 1;
        else {
            const unsigned char *src = data + 5;
            unsigned char *dst = S->frame + row * 48;
            for (unsigned i = 0; i < count * 48; ++i) dst[i] = src[i];
            if (data[2] & 1) present();
        }
    } else if (sub == 4) {
        unsigned colors = n >= 3 ? data[2] : 0;
        if (!colors || colors > 16 || n < 3 + colors * 3 + 128) status = 1;
        else {
            const unsigned char *pal = data + 3, *pix = pal + colors * 3;
            for (unsigned i = 0; i < 256; ++i) {
                unsigned idx = (pix[i >> 1] >> ((i & 1) ? 0 : 4)) & 15;
                if (idx >= colors) idx = 0;
                S->frame[i * 3] = pal[idx * 3];
                S->frame[i * 3 + 1] = pal[idx * 3 + 1];
                S->frame[i * 3 + 2] = pal[idx * 3 + 2];
            }
            present();
        }
    } else if (sub == 5) {
        if (n < 5) status = 1;
        else {
            for (unsigned i = 0; i < FRAME_BYTES; i += 3) {
                S->frame[i] = data[2]; S->frame[i + 1] = data[3]; S->frame[i + 2] = data[4];
            }
            present();
        }
    } else status = 3;

    /* Queued keys are copied into the reply but only removed from the queue
     * once the stock reply queue has taken the reply. Until then the key hook
     * sees a non-empty queue and queues new events instead of pushing them, so
     * a push can never overtake older keys, and a refused reply loses none. */
    unsigned count = 0, read = S->key_read;
    if (S->magic == MAGIC) {
        S->last_command = now;
        S->context = context;
        S->over_ble = stock_ble_connected != 0;
        while (count < REPLY_KEYS && read != S->key_write) {
            reply[5 + count * 2] = S->keys[read % QUEUE_SIZE][0];
            reply[6 + count * 2] = S->keys[read % QUEUE_SIZE][1];
            ++read;
            ++count;
        }
    }
    reply[0] = sub;
    reply[1] = status;
    reply[2] = mode_bits();
    reply[3] = S->magic == MAGIC ? (S->dropped > 255 ? 255 : S->dropped) : 0;
    reply[4] = count;
    unsigned char *p = reply + 5 + count * 2;
    if (battery) {
        unsigned level = stock_battery_level;
        *p++ = level > 255 ? 255 : level;
        *p++ = stock_usb_power != 0;
        *p++ = stock_charge_pin != 0;
    }
    if (stats) {
        void *task = stock_device_task;
        p = put16(p, S->pushed);
        p = put16(p, S->low_stack);
        p = put16(p, S->push_failed);
        p = put16(p, S->min_free);
        p = put16(p, task ? stock_stack_high_water(task) * 4 : 0xffff);
        p = put16(p, S->blocked);
    }
    if (stock_reply(context, 0xf0, reply, p - reply) && count) S->key_read = read;
}
