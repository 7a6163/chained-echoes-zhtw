"""
Faithful structural parser for BansheeGz BGDatabase binary format V7,
reverse-engineered from the game's BGDatabase.dll (BGRepoBinaryV7 / BGBinaryReader).

Goal: walk the whole file, locate every field's value-blob, decode the
per-field offset table + concatenated value bytes, so we can rewrite
specific (Chinese) string columns with new content and re-emit the file
by pure slice replacement (everything is self-delimiting, no absolute
offsets) -> safe.

Validation gate: after a full parse, the cursor MUST equal len(data).
"""
import struct

# from decompiled BGRepoBinaryV7
UNIQUE_ID = (5208396773359933591, 14401663668042484142)
ENCRYPTION_ID = (4770294628005998460, 9299804957405062829)


def _id_bytes(k1, k2):
    # BGId stores two int64 little-endian (ToInt64/ValueToBytes are LE)
    return struct.pack("<QQ", k1, k2)


class Reader:
    def __init__(self, data):
        self.d = data
        self.c = 0

    def int(self):
        v = struct.unpack_from("<i", self.d, self.c)[0]
        self.c += 4
        return v

    def ushort(self):
        v = struct.unpack_from("<H", self.d, self.c)[0]
        self.c += 2
        return v

    def bool(self):
        v = self.d[self.c]
        self.c += 1
        return v != 0

    def id(self):
        b = self.d[self.c:self.c + 16]
        self.c += 16
        return b

    def bytearray(self):
        n = self.int()
        off = self.c
        self.c += n
        return off, n

    def string(self):
        off, n = self.bytearray()
        if n == 0:
            return None
        return self.d[off:off + n].decode("utf-8")

    def array(self, fn):
        n = self.int()
        return [fn() for _ in range(n)]


class Field:
    __slots__ = ("meta", "name", "typecode", "val_off", "val_len")


def parse(data):
    r = Reader(data)
    version = r.int()
    assert version == 7, f"unexpected version {version}"
    uid = r.id()
    assert struct.unpack("<QQ", uid) == UNIQUE_ID, "not a BGDatabase file"

    # optional encryption header
    if r.c + 16 < len(data):
        peek = data[r.c:r.c + 16]
        if peek == _id_bytes(*ENCRYPTION_ID):
            raise SystemExit("file is encrypted - not handled")
        # else: not encryption, do not consume

    # addons
    def read_addon():
        ver = r.int()
        assert ver == 1, f"addon ver {ver}"
        typename = r.string()
        r.bytearray()  # config
        return typename
    addons = r.array(read_addon)

    fields = []

    def read_meta_header():
        ver = r.int()
        assert ver in (1, 2, 3), f"meta ver {ver}"
        if ver == 1:
            r.id()
            r.string(); r.string(); r.bytearray(); r.bool()
            r.string(); r.bool(); r.bool(); r.bool(); r.string()
            return "?"
        tc = r.ushort()
        if tc == 0:
            r.string()  # type
        r.id()
        name = r.string()
        r.bytearray()  # config
        r.bool()       # system
        r.string()     # addon
        r.bool(); r.bool(); r.bool()  # uniqueName, singleton, emptyName
        r.string()     # comment
        if ver == 3:
            r.bool()   # UserDefinedReadonly
        return name

    def read_field_header(meta_name):
        ver = r.int()
        assert ver in (1, 2, 3), f"field ver {ver}"
        f = Field()
        f.meta = meta_name
        if ver == 1:
            r.id(); f.name = r.string(); r.string(); r.bytearray(); r.bool()
            r.string(); r.string(); r.bool(); r.string(); r.string(); r.string()
            f.typecode = 0
            return f
        tc = r.ushort()
        f.typecode = tc
        if tc == 0:
            r.string()  # type
        r.id()
        f.name = r.string()
        r.bytearray()  # config
        r.bool()       # system
        r.string()     # addon
        r.string()     # defaultValue
        r.bool()       # required
        r.string(); r.string(); r.string()  # fmt, editor, comment
        if ver == 3:
            r.bool()
        return f

    def read_key():
        ver = r.int(); assert ver == 1
        r.id(); r.string(); r.bool()
        r.array(lambda: r.id())

    def read_index():
        ver = r.int(); assert ver == 1
        r.id(); r.string(); r.id()

    def read_meta():
        name = read_meta_header()
        r.bytearray()  # entity ids
        def read_field():
            f = read_field_header(name)
            off, n = r.bytearray()  # field values blob
            f.val_off, f.val_len = off, n
            fields.append(f)
        r.array(read_field)
        r.array(read_key)
        r.array(read_index)

    r.array(read_meta)

    def read_view():
        ver = r.int(); assert ver == 1
        r.id(); r.string(); r.bytearray(); r.string(); r.string(); r.bool()
        r.bytearray()  # nested repo
        r.array(lambda: r.id())
    r.array(read_view)

    return version, addons, fields, r.c


def decode_field_values(data, off, n):
    """Decode a variable-size field value blob -> list of (entityIndex, value_bytes).
    Returns None if it does not match the variable-size string layout."""
    if n == 0:
        return []
    num = struct.unpack_from("<i", data, off)[0]
    if num <= 0:
        return []
    table_off = off + 4
    if table_off + num * 8 > off + n:
        return None
    blob_off = table_off + num * 8
    out = []
    prev = 0
    for k in range(num):
        idx = struct.unpack_from("<i", data, table_off + k * 8)[0]
        end = struct.unpack_from("<i", data, table_off + k * 8 + 4)[0]
        vb = data[blob_off + prev: blob_off + end]
        out.append((idx, vb))
        prev = end
    if blob_off + prev != off + n:
        return None
    return out


if __name__ == "__main__":
    import sys
    data = open(sys.argv[1], "rb").read()
    version, addons, fields, cursor = parse(data)
    ok = cursor == len(data)
    print(f"file len={len(data)} cursor={cursor}  {'OK (cursor==len)' if ok else '!!! MISMATCH'}")
    print(f"version={version} addons={addons}")
    print(f"total fields={len(fields)}")
