"""Compare bulk operations with simple independent reference implementations."""
import random
import struct

import numpy as np
import pytest

from wgmap_backporter_studio.core import legacy1710_engine as e


def reference_decode(data,palette_size,count,min_bits,padded):
    bits=max(min_bits,(palette_size-1).bit_length());mask=(1<<bits)-1
    out=np.zeros(count,dtype=np.int32)
    if palette_size<=1 or not data:return out
    words=[int(x)&((1<<64)-1) for x in data]
    for i in range(count):
        if padded:word,slot=divmod(i,64//bits);shift=slot*bits
        else:word,shift=divmod(i*bits,64)
        if word>=len(words):break
        value=words[word]>>shift
        if not padded and shift+bits>64 and word+1<len(words):
            value |= words[word+1]<<(64-shift)
        value &=mask
        out[i]=value if value<palette_size else 0
    return out


@pytest.mark.parametrize("padded",[False,True])
@pytest.mark.parametrize("count,min_bits",[(64,1),(4096,4)])
def test_all_bit_widths_signed_unsigned_and_truncated(padded,count,min_bits):
    rng=random.Random(745)
    for bits in range(min_bits,13):
        palette_size=(1<<(bits-1))+1
        n=((count+(64//bits)-1)//(64//bits)) if padded else ((count*bits+63)//64)
        values=[rng.getrandbits(64) for _ in range(n)]
        for truncated in (False,True):
            words=values[:max(1,n//2)] if truncated else values
            for signed in (False,True):
                data=[v-(1<<64) if signed and v>=(1<<63) else v for v in words]
                expected=reference_decode(data,palette_size,count,min_bits,padded)
                assert np.array_equal(e.unpack_palette_indices(data,palette_size,count,min_bits,padded=padded),expected)
    assert not np.any(e.unpack_palette_indices([],4,count,min_bits,padded=padded))
    assert not np.any(e.unpack_palette_indices([123],1,count,min_bits,padded=padded))


def test_bulk_nbt_integer_arrays_preserve_signed_values():
    for tag_type,width,code,values in [(11,4,"i",[-2147483648,-1,0,2147483647]),(12,8,"q",[-9223372036854775808,-1,0,9223372036854775807])]:
        data=struct.pack(">i",len(values))+struct.pack(">%d%s"%(len(values),code),*values)
        assert e.read_payload(e.Reader(data),tag_type)==values
        with pytest.raises(EOFError):e.read_payload(e.Reader(data[:-1]),tag_type)
        with pytest.raises(e.ConversionError):e.read_payload(e.Reader(struct.pack(">i",-1)),tag_type)
    assert e.p_int_array([-1,0,256])==struct.pack(">iiii",3,-1,0,256)


def test_bulk_nibble_and_skylight_parity():
    rng=np.random.default_rng(772)
    for count in (0,1,2,2048,4096):
        values=rng.integers(0,16,size=count,dtype=np.uint8)
        padded=np.pad(values,(0,count%2))
        expected=bytes((padded[0::2] | (padded[1::2]<<4)).tolist())
        assert e.pack_nibbles(values)==expected
    fallback=rng.integers(0,257,size=(16,16),dtype=np.int32)
    for keys in ([0,3,15],[1],[0,1,2],[]):
        lights={k:e.pack_nibbles(rng.integers(0,16,size=4096,dtype=np.uint8)) for k in keys}
        expected=fallback.copy();known=np.zeros((16,16),dtype=bool)
        for ty,packed in sorted(lights.items(),reverse=True):
            vals=e.unpack_nibbles(packed).reshape(16,16,16)
            for ly in range(15,-1,-1):
                mask=(vals[ly]<15)&(~known)
                expected[mask]=ty*16+ly+1;known[mask]=True
        assert np.array_equal(e.derive_heightmap_from_skylight(lights,fallback),expected)
    assert np.array_equal(e.derive_heightmap_from_skylight({0:bytes([255])*2048},fallback),fallback)
