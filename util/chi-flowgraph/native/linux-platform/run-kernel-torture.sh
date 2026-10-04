#!/bin/sh
# Linux 6.6.52 module tests, run sequentially after the four-hart boot gate.
# This script emits evidence; the host check-kernel-torture.py decides PASS.
set -eu
export PATH=/bin:/sbin:/usr/bin:/usr/sbin
rcu_seconds=${CHI_RCU_SECONDS:-3}
lock_seconds=${CHI_LOCK_SECONDS:-2}
threads=${CHI_TORTURE_THREADS:-4}
lock_types=${CHI_LOCK_TYPES:-"spin_lock rw_lock mutex_lock rwsem_lock"}
module_dir=${CHI_TORTURE_MODULE_DIR:-/lib/modules/$(uname -r)/kernel}
evidence_dir=${CHI_TORTURE_EVIDENCE_DIR:-/tmp/chi-kernel-torture}
read uptime idle < /proc/uptime
run_id="kt-${uptime%.*}-$$"
fail() { echo "CHI_KERNEL_TORTURE_FAIL id=$run_id reason=$1"; exit 1; }
positive() {
    case "$2" in ''|*[!0-9]*) fail "invalid_$1";; esac
    [ "$2" -ge 1 ] && [ "$2" -le 3600 ] || fail "out_of_range_$1"
}
positive rcu_seconds "$rcu_seconds"
positive lock_seconds "$lock_seconds"
positive threads "$threads"
[ "$threads" -ge 2 ] && [ "$threads" -le 16 ] || fail thread_count
[ "$(cat /sys/devices/system/cpu/online)" = 0-3 ] || fail cpu_count
[ "$(cat /sys/devices/system/node/online)" = 0-1 ] || fail numa_count
[ -w /dev/kmsg ] || fail missing_kmsg
[ ! -d /sys/module/torture ] || fail existing_torture_module
[ ! -d /sys/module/rcutorture ] || fail existing_rcutorture_module
[ ! -d /sys/module/locktorture ] || fail existing_locktorture_module
seen=" "
for kind in $lock_types; do
    case "$kind" in spin_lock|rw_lock|mutex_lock|rwsem_lock) ;; *) fail invalid_lock_type;; esac
    case "$seen" in *" $kind "*) fail duplicate_lock_type;; esac
    seen="$seen$kind "
done
[ "$seen" != " " ] || fail empty_lock_types
mkdir -p "$evidence_dir"
echo "CHI_KERNEL_TORTURE_RUN_BEGIN id=$run_id kernel=$(uname -r) cpus=0-3 nodes=0-1 rcu_seconds=$rcu_seconds lock_seconds=$lock_seconds threads=$threads"
cleanup() {
    status=$?
    trap - EXIT
    [ ! -d /sys/module/rcutorture ] || rmmod rcutorture || true
    [ ! -d /sys/module/locktorture ] || rmmod locktorture || true
    [ ! -d /sys/module/torture ] || rmmod torture || true
    if [ "$status" -ne 0 ]; then
        echo "CHI_KERNEL_TORTURE_FAIL id=$run_id reason=runner_exit_$status"
    fi
    exit "$status"
}
trap cleanup EXIT
insmod "$module_dir/kernel/torture.ko" || fail load_common_module

run_case() {
    test_name=$1
    duration=$2
    module_name=$3
    module_file=$4
    shift 4
    read before unused < /proc/uptime
    echo "CHI_KERNEL_TORTURE_CASE_BEGIN id=$run_id test=$test_name seconds=$duration uptime=$before"
    echo "CHI_KERNEL_TORTURE_PARAMETERS id=$run_id test=$test_name $*"
    printf '<6>CHI_KERNEL_TORTURE_KMSG_BEGIN id=%s test=%s\n' "$run_id" "$test_name" > /dev/kmsg
    insmod "$module_file" "$@" || fail "load_$test_name"
    sleep "$duration" || fail "sleep_$test_name"
    rmmod "$module_name" || fail "unload_$test_name"
    read after unused < /proc/uptime
    printf '<6>CHI_KERNEL_TORTURE_KMSG_END id=%s test=%s\n' "$run_id" "$test_name" > /dev/kmsg
    dmesg > "$evidence_dir/$test_name.dmesg" || fail "capture_$test_name"
    echo "CHI_KERNEL_TORTURE_LOG_BEGIN id=$run_id test=$test_name"
    cat "$evidence_dir/$test_name.dmesg"
    echo "CHI_KERNEL_TORTURE_LOG_END id=$run_id test=$test_name"
    echo "CHI_KERNEL_TORTURE_CASE_END id=$run_id test=$test_name seconds=$duration uptime=$after insmod_rc=0 rmmod_rc=0"
}

# No injected stalls, hotplug, stuttering, or automatic shutdown. The explicit
# RCU profile exercises readers, ordinary/synchronous GPs, and callback barriers.
run_case rcu "$rcu_seconds" rcutorture "$module_dir/kernel/rcu/rcutorture.ko" \
    torture_type=rcu nreaders="$threads" nfakewriters=2 n_barrier_cbs="$threads" \
    gp_normal=1 gp_sync=1 irqreader=1 fwd_progress=0 test_boost=0 read_exit_burst=0 \
    stat_interval=1 stutter=0 shuffle_interval=0 onoff_interval=0 shutdown_secs=0
for kind in $lock_types; do
    readers=0
    case "$kind" in rw_lock|rwsem_lock) readers=$threads;; esac
    run_case "$kind" "$lock_seconds" locktorture "$module_dir/kernel/locking/locktorture.ko" \
        torture_type="$kind" nwriters_stress="$threads" nreaders_stress="$readers" \
        long_hold=0 rt_boost=0 stat_interval=1 stutter=0 shuffle_interval=0 \
        onoff_interval=0 shutdown_secs=0
done
rmmod torture || fail unload_common_module
echo "CHI_KERNEL_TORTURE_RUN_END id=$run_id status=completed"
