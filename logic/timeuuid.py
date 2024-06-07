from __future__ import division, absolute_import, unicode_literals


import struct
import uuid

# UUID 1 format
# time_low                the first 32 bits of the UUID
# time_mid                the next 16 bits of the UUID
# time_hi_version         the next 16 bits of the UUID
# clock_seq_hi_variant    the next 8 bits of the UUID
# clock_seq_low           the next 8 bits of the UUID
# node                    the last 48 bits of the UUID

def pack_timeuuid_binary(u):
    """
    Re-order a UUID1's bytes into a big endian word that sorts in time-order.

    This makes sort-order correct when storing a TimeUUID in DynamoDB.

    """
    parts = struct.unpack(b'<LHHBB6s', u.bytes)
    if parts[2]>>15 != 1:
        raise Exception('Not a type 1 UUID (TimeUUID)')
    return struct.pack(b'HHLBB6s',
                       parts[2], parts[1], parts[0],
                       parts[3], parts[4], parts[5])

def unpack_timeuuid_binary(s):
    parts = struct.unpack(b'HHLBB6s', s)
    bytes = struct.pack(b'<LHHBB6s',
                        parts[2], parts[1], parts[0],
                        parts[3], parts[4], parts[5])
    return uuid.UUID(bytes=bytes)
