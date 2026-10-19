# QR code encoder: byte mode, error correction level M, versions 1-10, so the
# startup banner can show a scannable link without third-party packages.

ALIGNMENT = {
    1: [],
    2: [6, 18],
    3: [6, 22],
    4: [6, 26],
    5: [6, 30],
    6: [6, 34],
    7: [6, 22, 38],
    8: [6, 24, 42],
    9: [6, 26, 46],
    10: [6, 28, 50],
}

BLOCKS = {
    1: (10, [(1, 16)]),
    2: (16, [(1, 28)]),
    3: (26, [(1, 44)]),
    4: (18, [(2, 32)]),
    5: (24, [(2, 43)]),
    6: (16, [(4, 27)]),
    7: (18, [(4, 31)]),
    8: (22, [(2, 38), (2, 39)]),
    9: (22, [(3, 36), (2, 37)]),
    10: (26, [(4, 43), (1, 44)]),
}

REMAINDER = {1: 0, 2: 7, 3: 7, 4: 7, 5: 7, 6: 7, 7: 0, 8: 0, 9: 0, 10: 0}

FORMAT_MASK = 0x5412
FORMAT_GENERATOR = 0x537
VERSION_GENERATOR = 0x1F25


class QRError(ValueError):
    pass


def _bch(value, generator):
    shift = generator.bit_length() - 1
    rest = value << shift
    while rest.bit_length() >= generator.bit_length():
        rest ^= generator << (rest.bit_length() - generator.bit_length())
    return rest


def _data_capacity(version):
    ec, groups = BLOCKS[version]
    data = sum(count * size for count, size in groups)
    return data, ec


def _codewords(version, payload):
    data_len, _ = _data_capacity(version)
    bits = []
    for byte in payload:
        for i in range(7, -1, -1):
            bits.append((byte >> i) & 1)
    count_bits = 8 if version <= 9 else 16
    header = []
    for value, width in ((0b0100, 4), (len(payload), count_bits)):
        for i in range(width - 1, -1, -1):
            header.append((value >> i) & 1)
    bits = header + bits
    capacity = data_len * 8
    if len(bits) > capacity:
        raise QRError(f"payload of {len(payload)} bytes does not fit version {version}")
    bits += [0] * min(4, capacity - len(bits))
    while len(bits) % 8:
        bits.append(0)
    words = [int("".join(str(b) for b in bits[i:i + 8]), 2) for i in range(0, len(bits), 8)]
    pad = (0xEC, 0x11)
    while len(words) < data_len:
        words.append(pad[len(words) % 2])
    return words


def _blocks(version, data_words):
    ec_len = BLOCKS[version][0]
    groups = BLOCKS[version][1]
    blocks = []
    index = 0
    for count, size in groups:
        for _ in range(count):
            blocks.append(data_words[index:index + size])
            index += size
    ecc = []
    for block in blocks:
        ecc.append(_reed_solomon(block, ec_len))
    out = []
    longest = max(len(b) for b in blocks)
    for i in range(longest):
        for block in blocks:
            if i < len(block):
                out.append(block[i])
    longest_ec = max(len(e) for e in ecc)
    for i in range(longest_ec):
        for e in ecc:
            if i < len(e):
                out.append(e[i])
    return out


def _gf_tables():
    exp = [0] * 512
    log = [0] * 256
    value = 1
    for i in range(255):
        exp[i] = value
        log[value] = i
        value <<= 1
        if value & 0x100:
            value ^= 0x11D
    for i in range(255, 512):
        exp[i] = exp[i - 255]
    return exp, log


GF_EXP, GF_LOG = _gf_tables()


def _gf_mul(a, b):
    if a == 0 or b == 0:
        return 0
    return GF_EXP[GF_LOG[a] + GF_LOG[b]]


def _rs_generator(degree):
    poly = [1]
    for i in range(degree):
        nxt = [0] * (len(poly) + 1)
        root = GF_EXP[i]
        for j, coef in enumerate(poly):
            nxt[j] ^= coef
            nxt[j + 1] ^= _gf_mul(coef, root)
        poly = nxt
    return poly


def _reed_solomon(data, ec_len):
    generator = _rs_generator(ec_len)
    rest = list(data) + [0] * ec_len
    for i in range(len(data)):
        coef = rest[i]
        if coef == 0:
            continue
        for j, g in enumerate(generator):
            rest[i + j] ^= _gf_mul(g, coef)
    return rest[len(data):]


class _Matrix:
    def __init__(self, version):
        self.version = version
        self.size = version * 4 + 17
        self.cells = [[None] * self.size for _ in range(self.size)]
        self.reserved = [[False] * self.size for _ in range(self.size)]

    def set(self, row, col, value, reserve=True):
        if 0 <= row < self.size and 0 <= col < self.size:
            self.cells[row][col] = value
            if reserve:
                self.reserved[row][col] = True

    def finder(self, row, col):
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                r, c = row + dr, col + dc
                if not (0 <= r < self.size and 0 <= c < self.size):
                    continue
                inner = 0 <= dr <= 6 and 0 <= dc <= 6
                dark = inner and (dr in (0, 6) or dc in (0, 6) or (2 <= dr <= 4 and 2 <= dc <= 4))
                self.set(r, c, 1 if dark else 0)

    def alignment(self, row, col):
        for dr in range(-2, 3):
            for dc in range(-2, 3):
                dark = max(abs(dr), abs(dc)) != 1
                self.set(row + dr, col + dc, 1 if dark else 0)

    def timing(self):
        for i in range(8, self.size - 8):
            bit = 1 if i % 2 == 0 else 0
            self.set(6, i, bit)
            self.set(i, 6, bit)

    def reserve_format(self):
        for i in range(9):
            self.set(8, i, 0)
            self.set(i, 8, 0)
        for i in range(8):
            self.set(8, self.size - 1 - i, 0)
            self.set(self.size - 1 - i, 8, 0)
        self.set(8, 8, 0)
        self.set(self.size - 8, 8, 0)

    def reserve_version(self):
        if self.version < 7:
            return
        for i in range(18):
            self.set(i // 3, i % 3 + self.size - 11, 0)
            self.set(i % 3 + self.size - 11, i // 3, 0)

    def place_data(self, codewords):
        bits = []
        for word in codewords:
            for i in range(7, -1, -1):
                bits.append((word >> i) & 1)
        bits += [0] * REMAINDER[self.version]
        index = 0
        upward = True
        col = self.size - 1
        while col > 0:
            if col == 6:
                col -= 1
            rows = range(self.size - 1, -1, -1) if upward else range(self.size)
            for row in rows:
                for c in (col, col - 1):
                    if self.reserved[row][c]:
                        continue
                    self.cells[row][c] = bits[index] if index < len(bits) else 0
                    index += 1
            upward = not upward
            col -= 2

    def mask(self, pattern):
        for row in range(self.size):
            for col in range(self.size):
                if self.reserved[row][col]:
                    continue
                if _mask_bit(pattern, row, col):
                    self.cells[row][col] ^= 1

    def format_info(self, pattern):
        data = (0b00 << 3) | pattern
        bits = ((data << 10) | _bch(data, FORMAT_GENERATOR)) ^ FORMAT_MASK
        for i in range(15):
            bit = (bits >> i) & 1
            if i < 6:
                self.set(i, 8, bit)
            elif i < 8:
                self.set(i + 1, 8, bit)
            else:
                self.set(self.size - 15 + i, 8, bit)
            if i < 8:
                self.set(8, self.size - 1 - i, bit)
            elif i == 8:
                self.set(8, 15 - i, bit)
            else:
                self.set(8, 15 - i - 1, bit)
        self.set(self.size - 8, 8, 1)

    def version_info(self):
        if self.version < 7:
            return
        bits = (self.version << 12) | _bch(self.version, VERSION_GENERATOR)
        for i in range(18):
            bit = (bits >> i) & 1
            self.set(i // 3, i % 3 + self.size - 11, bit)
            self.set(i % 3 + self.size - 11, i // 3, bit)

    def copy(self):
        clone = _Matrix(self.version)
        clone.cells = [row[:] for row in self.cells]
        clone.reserved = [row[:] for row in self.reserved]
        return clone

    def drawn(self):
        return [[1 if cell else 0 for cell in row] for row in self.cells]


def _mask_bit(pattern, row, col):
    if pattern == 0:
        return (row + col) % 2 == 0
    if pattern == 1:
        return row % 2 == 0
    if pattern == 2:
        return col % 3 == 0
    if pattern == 3:
        return (row + col) % 3 == 0
    if pattern == 4:
        return (row // 2 + col // 3) % 2 == 0
    if pattern == 5:
        return (row * col) % 2 + (row * col) % 3 == 0
    if pattern == 6:
        return ((row * col) % 2 + (row * col) % 3) % 2 == 0
    return ((row + col) % 2 + (row * col) % 3) % 2 == 0


def _penalty(cells):
    size = len(cells)
    score = 0
    for line in list(cells) + [list(col) for col in zip(*cells)]:
        run = 1
        for i in range(1, size):
            if line[i] == line[i - 1]:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run = 1
        if run >= 5:
            score += 3 + (run - 5)
    for row in range(size - 1):
        for col in range(size - 1):
            block = {cells[row][col], cells[row + 1][col], cells[row][col + 1], cells[row + 1][col + 1]}
            if len(block) == 1:
                score += 3
    tail = [0, 0, 0, 0]
    for line in list(cells) + [list(col) for col in zip(*cells)]:
        for i in range(size - 10):
            window = line[i:i + 11]
            if window == [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0] or window == [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1]:
                score += 40
    dark = sum(sum(row) for row in cells)
    ratio = dark * 100 // (size * size)
    score += 10 * (abs(ratio - 50) // 5)
    return score


def _pick_version(payload):
    for version in sorted(BLOCKS):
        data_len, _ = _data_capacity(version)
        count_bits = 8 if version <= 9 else 16
        needed = 4 + count_bits + len(payload) * 8
        if needed <= data_len * 8:
            return version
    raise QRError(f"payload of {len(payload)} bytes is too long")


def encode(text, version=None):
    payload = text.encode("utf-8")
    if version is None:
        version = _pick_version(payload)
    matrix = _Matrix(version)
    matrix.finder(0, 0)
    matrix.finder(0, matrix.size - 7)
    matrix.finder(matrix.size - 7, 0)
    matrix.timing()
    positions = ALIGNMENT[version]
    for row in positions:
        for col in positions:
            on_finder = (row <= 8 and col <= 8) or (row <= 8 and col >= matrix.size - 9) or (row >= matrix.size - 9 and col <= 8)
            if on_finder:
                continue
            matrix.alignment(row, col)
    matrix.reserve_format()
    matrix.reserve_version()
    matrix.place_data(_blocks(version, _codewords(version, payload)))
    best = None
    best_score = None
    for pattern in range(8):
        candidate = matrix.copy()
        candidate.mask(pattern)
        candidate.format_info(pattern)
        candidate.version_info()
        cells = candidate.drawn()
        score = _penalty(cells)
        if best_score is None or score < best_score:
            best, best_score = candidate, score
    return best.drawn()


def render(cells, quiet=4):
    count = len(cells) + quiet * 2
    lines = []
    for row in range(count):
        parts = []
        for col in range(count):
            r, c = row - quiet, col - quiet
            dark = 0 <= r < len(cells) and 0 <= c < len(cells) and cells[r][c]
            parts.append("  " if dark else "\u2588\u2588")
        lines.append("\x1b[40;37;1m" + "".join(parts) + "\x1b[0m")
    return "\n".join(lines)


def to_terminal(text, quiet=4):
    return render(encode(text), quiet=quiet)
