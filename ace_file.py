"""Host-independent ACEP2 project container (CBOR in a Zstandard frame)."""
import base64
import hashlib
import io
import json
import struct
from pathlib import Path

import cbor2
import zstandard


HEADER = struct.Struct('<5sBHQQQH')


def load_project(path):
    data = Path(path).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if data.startswith(b'ACEP2'):
        if len(data) < HEADER.size:
            raise ValueError('ACEP2 文件头不完整。')
        _, flag, _, offset, packed, size, metadata = HEADER.unpack_from(data)
        if (flag != 1 or offset < HEADER.size + metadata + 17 or offset + packed != len(data)
                or not size or data[HEADER.size + metadata] != 1):
            raise ValueError('ACEP2 工程头或数据长度无效。')
        with zstandard.ZstdDecompressor().stream_reader(memoryview(data)[offset:]) as stream:
            document = cbor2.CBORDecoder(stream).decode()
            if stream.tell() != size or stream.read(1):
                raise ValueError('ACEP2 工程内容长度不符。')
    else:
        wrapper = json.loads(data)
        if wrapper.get('version', 0) < 1000 or wrapper.get('compressMethod') != 'zstd':
            raise ValueError('当前离线读取支持 ACEP2 和 version 1000 的 Zstandard 工程。')
        packed = base64.b64decode(wrapper['content'])
        with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(packed)) as stream:
            document = json.load(stream)
    if not isinstance(document, dict) or not all(key in document for key in ('tracks','tempos','master')):
        raise ValueError('文件中缺少 ACE 轨道或速度信息。')
    return document, digest


def save_project(path, project):
    # Public interchange variant of ACEP2, with empty optional metadata.
    content = cbor2.dumps(project)
    packed = zstandard.ZstdCompressor(level=3, write_checksum=True).compress(content)
    offset, metadata_size = 192, 138
    header = HEADER.pack(b'ACEP2', 1, 0, offset, len(packed), len(content), metadata_size)
    header += bytes(metadata_size) + bytes([1]) + bytes(16)
    with Path(path).open('xb') as stream:
        stream.write(header.ljust(offset, b'\0'))
        stream.write(packed)
    actual, _ = load_project(path)
    if actual != project:
        raise ValueError('ACE 工程写入后的轨道或片段内容不一致。')
