"""Locks a rule's process on Linux, then runs its command. sandbox.py starts it on Linux hosts.

Usage: jail.py FOLDER NETWORK -- COMMAND...

Landlock lets the process write only in its folder and read only the system folders, so it cannot
read the API key or another rule. Seccomp refuses every internet socket, so it has no network,
except while pip installs libraries (NETWORK is 1). Both need no root and no system change, and
both stay on through exec, for the command and every child.
"""

import ctypes
import os
import platform
import struct
import sys

LIBC = ctypes.CDLL(None, use_errno=True)
PR_SET_NO_NEW_PRIVS = 38
PR_SET_SECCOMP = 22
SECCOMP_MODE_FILTER = 2
SYSCALLS = {  # machine: (landlock_create_ruleset, landlock_add_rule, landlock_restrict_self, socket, io_uring_setup)
    "aarch64": (444, 445, 446, 198, 425),
    "x86_64": (444, 445, 446, 41, 425),
}
AUDIT_ARCH = {"aarch64": 0xC00000B7, "x86_64": 0xC000003E}
AF_UNIX = 1
EACCES = 13

# Landlock file rights. A right that this kernel does not know is left out.
EXECUTE, WRITE_FILE, READ_FILE, READ_DIR = 1 << 0, 1 << 1, 1 << 2, 1 << 3
CHANGE = sum(1 << bit for bit in range(4, 13))  # remove and make files, folders, links and devices
REFER, TRUNCATE, IOCTL_DEV = 1 << 13, 1 << 14, 1 << 15
READ = EXECUTE | READ_FILE | READ_DIR
TCP_BIND, TCP_CONNECT = 1 << 0, 1 << 1
SCOPE_ABSTRACT_UNIX, SCOPE_SIGNAL = 1 << 0, 1 << 1
SYSTEM = ("/usr", "/etc", "/proc", "/sys", "/dev", "/run/systemd/resolve", "/var/lib/ca-certificates")


def fail(text):
    sys.stderr.write(f"jail: {text}\n")
    sys.exit(126)


def syscall(number, *args):
    result = LIBC.syscall(ctypes.c_long(number), *args)
    if result < 0:
        fail(f"system call {number} failed: {os.strerror(ctypes.get_errno())}")
    return result


def landlock(numbers, folder, network):
    create, add, restrict = numbers[:3]
    abi = LIBC.syscall(ctypes.c_long(create), None, ctypes.c_size_t(0), ctypes.c_uint32(1))
    if abi < 1:
        fail("this kernel has no Landlock, so the rule cannot run safely")
    handled = READ | WRITE_FILE | CHANGE | (REFER if abi >= 2 else 0) | (TRUNCATE if abi >= 3 else 0) | \
        (IOCTL_DEV if abi >= 5 else 0)
    attr = struct.pack("=Q", handled)
    if abi >= 4:
        # With no net rule, a handled right is refused: no TCP connect or bind.
        attr += struct.pack("=Q", 0 if network else TCP_BIND | TCP_CONNECT)
    if abi >= 6:
        # The rule cannot signal processes outside its sandbox, such as the server.
        attr += struct.pack("=Q", SCOPE_ABSTRACT_UNIX | SCOPE_SIGNAL)
    ruleset = syscall(create, ctypes.c_char_p(attr), ctypes.c_size_t(len(attr)), ctypes.c_uint32(0))

    def allow(path, rights):
        try:
            fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
        except OSError:
            return
        try:
            if not os.path.isdir(path):
                # A file can only get file rights. Folder rights on a file are an error.
                rights &= EXECUTE | WRITE_FILE | READ_FILE | TRUNCATE | IOCTL_DEV
            rule = struct.pack("=Qi", rights & handled, fd)
            syscall(add, ctypes.c_int(ruleset), ctypes.c_int(1), ctypes.c_char_p(rule), ctypes.c_uint32(0))
        finally:
            os.close(fd)

    for path in SYSTEM:
        allow(path, READ | (IOCTL_DEV if path == "/dev" else 0))
    allow("/dev/null", READ | WRITE_FILE)
    allow(folder, handled)
    if LIBC.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        fail("cannot set no_new_privs")
    syscall(restrict, ctypes.c_int(ruleset), ctypes.c_uint32(0))
    os.close(ruleset)


def no_network(numbers, machine):
    """A seccomp filter: socket() works only for local Unix sockets, and io_uring is refused."""
    socket_nr, uring_nr = numbers[3:]

    def op(code, jt, jf, k):
        return struct.pack("=HBBI", code, jt, jf, k)

    allow, refuse, kill = 0x7FFF0000, 0x00050000 | EACCES, 0x80000000
    program = b"".join([
        op(0x20, 0, 0, 4),                     # load the arch
        op(0x15, 1, 0, AUDIT_ARCH[machine]),   # same arch: skip the kill
        op(0x06, 0, 0, kill),
        op(0x20, 0, 0, 0),                     # load the syscall number
        op(0x15, 0, 1, uring_nr),
        op(0x06, 0, 0, refuse),
        op(0x15, 1, 0, socket_nr),
        op(0x06, 0, 0, allow),
        op(0x20, 0, 0, 16),                    # load the socket's family
        op(0x15, 0, 1, AF_UNIX),
        op(0x06, 0, 0, allow),
        op(0x06, 0, 0, refuse),
    ])
    filters = ctypes.create_string_buffer(program)
    fprog = struct.pack("=HxxxxxxQ", len(program) // 8, ctypes.addressof(filters))
    if LIBC.prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, ctypes.c_char_p(fprog), 0, 0) != 0:
        fail(f"cannot load the network filter: {os.strerror(ctypes.get_errno())}")


def main():
    if len(sys.argv) < 5 or sys.argv[3] != "--":
        fail("usage: jail.py FOLDER NETWORK -- COMMAND...")
    folder, network, command = os.path.realpath(sys.argv[1]), sys.argv[2] == "1", sys.argv[4:]
    machine = platform.machine()
    numbers = SYSCALLS.get(machine)
    if not numbers:
        fail(f"no sandbox for {machine}")
    landlock(numbers, folder, network)
    if not network:
        no_network(numbers, machine)
    os.chdir(folder)
    try:
        os.execvpe(command[0], command, os.environ)
    except OSError as e:
        fail(f"cannot run {command[0]}: {e.strerror}")


if __name__ == "__main__":
    main()
