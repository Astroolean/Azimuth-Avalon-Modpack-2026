"""Bounded reader for installed publisher class evidence; never executes config.

Format: https://community.bistudio.com/wiki/raP_File_Format_-_Elite
Only concrete declared CfgVehicles classes are consumed by the economy adapter.
Text preprocessing is deliberately unsupported and reported to the caller.
"""
from __future__ import annotations
import struct, re

class DecodeError(ValueError): pass

def node(name='', parent=''):
    return {'name':name,'parent':parent,'properties':{},'classes':{},'extern':[],'deleted':[],'entries':[]}

def add(n,e):
    n['entries'].append({k:v for k,v in e.items() if k != 'body'})
    if e['kind']=='class': n['classes'][e['name']]=e['body']
    elif e['kind']=='property': n['properties'][e['name']]=e['value']
    elif e['kind']=='extern': n['extern'].append(e['name'])
    elif e['kind']=='delete': n['deleted'].append(e['name'])

class Rap:
    def __init__(self,data):
        self.data=data; self.pos=0; self.active=set(); self.total=0
    def take(self,n):
        if n<0 or self.pos+n>len(self.data): raise DecodeError(f'Out of bounds at {self.pos}, need {n}')
        v=self.data[self.pos:self.pos+n]; self.pos+=n;return v
    def u8(self): return self.take(1)[0]
    def u32(self): return struct.unpack('<I',self.take(4))[0]
    def i32(self): return struct.unpack('<i',self.take(4))[0]
    def string(self):
        end=self.data.find(b'\x00',self.pos)
        if end<0: raise DecodeError(f'Unterminated string at {self.pos}')
        out=self.take(end-self.pos);self.take(1)
        try:return out.decode('utf-8')
        except UnicodeDecodeError:return out.decode('cp1252')
    def varint(self):
        v=0
        for s in range(0,35,7):
            b=self.u8();v|=(b&127)<<s
            if not b&128:
                if v>1000000:raise DecodeError('Unreasonable entry count')
                return v
        raise DecodeError('Varint overflow')
    def value(self,t,depth):
        if depth>100:raise DecodeError('Array depth exceeded')
        if t==0:return self.string()
        if t==1:return struct.unpack('<f',self.take(4))[0]
        if t==2:return self.i32()
        if t==3:return self.array(depth+1)
        if t==4:return {'variable':self.string()}
        raise DecodeError(f'Unknown scalar type {t} at {self.pos-1}')
    def array(self,depth):return [self.value(self.u8(),depth) for _ in range(self.varint())]
    def body(self,offset,name='',depth=0):
        if depth>100 or offset in self.active:raise DecodeError('Class cycle/depth exceeded')
        if offset<16 or offset>=self.enum_offset:raise DecodeError(f'Invalid class offset {offset}')
        self.active.add(offset); self.pos=offset
        n=node(name,self.string());n['offset']=offset
        count=self.varint();self.total+=count
        if self.total>1000000:raise DecodeError('Entry limit')
        for i in range(count):
            at=self.pos;t=self.u8()
            if t==0:
                key=self.string();ptr=self.u32();saved=self.pos
                sub=self.body(ptr,key,depth+1);self.pos=saved
                add(n,{'kind':'class','name':key,'body':sub})
            elif t==1:
                sub=self.u8();key=self.string();value=self.value(sub,depth)
                add(n,{'kind':'property','name':key,'value':value,'value_type':sub})
            elif t in (2,5):
                flags=self.u32() if t==5 else 0
                key=self.string();value=self.array(depth)
                add(n,{'kind':'property','name':key,'value':value,'array':True,'flags':flags,'append':bool(flags&1)})
            elif t in (3,4):add(n,{'kind':'extern' if t==3 else 'delete','name':self.string()})
            else:raise DecodeError(f'Unknown entry type {t} at {at}')
        if self.pos>self.enum_offset:raise DecodeError('Class overlaps enums')
        self.active.remove(offset);return n
    def decode(self):
        if self.take(4)!=b'\x00raP':raise DecodeError('No RAP signature')
        z=self.u32();eight=self.u32();self.enum_offset=self.u32()
        if z!=0 or eight!=8:raise DecodeError(f'Unsupported RAP header {z}, {eight}')
        if self.enum_offset<16 or self.enum_offset+4>len(self.data):raise DecodeError('Invalid enums offset')
        root=self.body(16)
        self.pos=self.enum_offset;count=self.u32()
        if count>100000:raise DecodeError('Enum limit')
        enums={}
        for _ in range(count):
            key=self.string();enums[key]=self.i32()
        return {'format':'rap','root':root,'enums':enums,'trailing_bytes':len(self.data)-self.pos}

TOKEN=re.compile(r'\s+|//[^\n]*|/\*[\s\S]*?\*/|"(?:""|[^"\n])*"|\+=|[{}\[\];:=,]|[^\s{}\[\];:=,"/]+|/',re.M)
class Text:
    def __init__(self,s):
        if len(s) > 4 * 1024 * 1024:
            raise DecodeError("Text config exceeds limit")
        self.depth = 0
        # Record preprocessor lines without pretending to expand them.
        self.directives=[line for line in s.splitlines() if line.lstrip().startswith('#')]
        s='\n'.join(line for line in s.splitlines() if not line.lstrip().startswith('#'))
        self.tokens=[m.group(0) for m in TOKEN.finditer(s) if not m.group(0).isspace() and not m.group(0).startswith(('//','/*'))];self.pos=0
    def peek(self):return self.tokens[self.pos] if self.pos<len(self.tokens) else None
    def take(self,expected=None):
        t=self.peek()
        if t is None:raise DecodeError('Unexpected text EOF')
        if expected is not None and t!=expected:raise DecodeError(f'Expected {expected!r}, got {t!r}, token {self.pos}')
        self.pos+=1;return t
    def value(self):
        if self.peek()=='{':
            self.take();a=[]
            while self.peek()!='}':
                a.append(self.value())
                if self.peek()==',':self.take()
                elif self.peek()!='}':raise DecodeError('Bad array separator')
            self.take();return a
        words=[]
        while self.peek() is not None and self.peek() not in (';',',','}'):
            words.append(self.take())
        if not words:raise DecodeError('Empty scalar')
        if len(words)==1:
            t=words[0]
            if t.startswith('"'):return t[1:-1].replace('""','"')
            try:return int(t,0) if not re.search('[.eE]',t) else float(t)
            except ValueError:return {'expression':t}
        return {'expression':' '.join(words)}
    def body(self,name='',parent='',nested=False):
        self.depth += 1
        if self.depth > 64:
            raise DecodeError("Text config class depth exceeded")
        n=node(name,parent)
        while self.peek() is not None and self.peek()!='}':
            t=self.take()
            if t==';':continue
            if t=='class':
                key=self.take();parent=''
                if self.peek()==':':self.take();parent=self.take()
                if self.peek()==';':self.take();add(n,{'kind':'extern','name':key});continue
                self.take('{');sub=self.body(key,parent,True);self.take('}')
                if self.peek()==';':self.take()
                add(n,{'kind':'class','name':key,'body':sub})
            elif t=='delete':add(n,{'kind':'delete','name':self.take()});self.take(';')
            else:
                array=False
                if self.peek()=='[':self.take();self.take(']');array=True
                op=self.take()
                if op not in ('=','+='):raise DecodeError(f'Invalid property operator {op!r}')
                v=self.value();self.take(';');add(n,{'kind':'property','name':t,'value':v,'array':array,'append':op=='+=','flags':int(op=='+=')})
        self.depth -= 1
        return n
    def decode(self):
        root=self.body()
        if self.pos!=len(self.tokens):raise DecodeError('Unexpected top-level closing brace')
        return {'format':'text','root':root,'preprocessor_directives':self.directives,'macro_expansion':False}

def decode(data):
    if len(data) > 4 * 1024 * 1024:
        raise DecodeError("Config exceeds limit")
    if data.startswith(b'\x00raP'):return Rap(data).decode()
    try:s=data.decode('utf-8-sig')
    except UnicodeDecodeError:s=data.decode('cp1252')
    return Text(s).decode()
