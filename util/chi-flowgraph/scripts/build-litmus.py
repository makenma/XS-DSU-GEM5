"""Reproducible freestanding RV64 programs; debug line information maps PCs."""
import json
import pathlib
import subprocess
project=pathlib.Path(__file__).resolve().parent.parent
out=project/'build/litmus';out.mkdir(parents=True,exist_ok=True)
gcc_version=subprocess.check_output(['riscv64-linux-gnu-gcc','-dumpfullversion'],text=True).strip()
ld_version=subprocess.check_output(['riscv64-linux-gnu-ld','--version'],text=True).splitlines()[0]
if gcc_version!='13.3.0' or not ld_version.endswith('2.42'):
    raise RuntimeError(f'Pinned toolchain requires GCC 13.3.0 / binutils 2.42; found {gcc_version} / {ld_version}')
(out/'toolchain.json').write_text(json.dumps({'gcc':gcc_version,'binutils':ld_version,'isa':'rv64imafd','abi':'lp64d','compressedInstructions':False}))
sources=[];pc_map={}
for hart in range(4):
    body=['.option norvc','.text','.global _start','_start:','  li s0, 0x100000000','  li s1, 0x200000000','  li t0, 1']
    if hart==0: body+=['  sd t0, 0(s0) # x = 1']
    if hart==1: body+=['  sd t0, 64(s0) # y = 1']
    if hart==2: body+=['  ld t1, 0(s0) # r0 = x','  ld t2, 64(s0) # r1 = y','  sd t1, 1024(s1)','  sd t2, 1032(s1)']
    if hart==3: body+=['  ld t1, 64(s0) # r0 = y','  ld t2, 0(s0) # r1 = x','  sd t1, 1024(s1)','  sd t2, 1032(s1)']
    body+=['  fence rw, rw','  sd t0, 2048(s1)','1: j 1b']
    # 2048 is outside a signed 12-bit immediate.
    body=[line.replace('sd t0, 2048(s1)','addi t3, s1, 1024\n  sd t0, 1024(t3)') for line in body]
    path=out/f'iriw-{hart}.S';path.write_text('\n'.join(body)+'\n')
    binary=out/f'iriw-{hart}.elf'
    subprocess.run(['riscv64-linux-gnu-gcc','-g','-nostdlib','-static','-march=rv64imafd','-mabi=lp64d','-Wl,-Ttext=0x10000','-Wl,--build-id=none',str(path),'-o',str(binary)],check=True)
    text=path.read_text();source=f'thread-{hart}';sources.append({'id':source,'hartId':hart,'node':f'RN{hart}','title':f'P{hart}','code':text,'language':'riscv'})
    disassembly=subprocess.check_output(['riscv64-linux-gnu-objdump','-d',str(binary)],text=True)
    addresses=[]
    import re
    for line in disassembly.splitlines():
        m=re.match(r'\s*([0-9a-f]+):\s+[0-9a-f]+\s+',line)
        if m: addresses.append(int(m[1],16))
    lines=subprocess.check_output(['riscv64-linux-gnu-addr2line','-e',str(binary),*[hex(a) for a in addresses]],text=True).splitlines()
    for address,line in zip(addresses,lines):
        n=re.search(r':(\d+)(?:\s|$)',line)
        if n: pc_map[f'{hart}:{hex(address)}']={'sourceId':source,'line':int(n[1])}
(out/'iriw-sources.json').write_text(json.dumps({'name':'IRIW','threads':sources,'pcMap':pc_map,'initial':{'x':0,'y':0},'results':{},'seed':1},ensure_ascii=False))
print('Built 4 RV64 IRIW ELF files with GCC:',subprocess.check_output(['riscv64-linux-gnu-gcc','-dumpfullversion'],text=True).strip())
def build_c(variant):
  sources=[];pc_map={}
  path=project/f'native/gem5/{variant}-test.c'
  for hart in range(4):
    binary=out/f'{variant}-{hart}.elf'
    subprocess.run(['riscv64-linux-gnu-gcc','-g','-O1','-nostdlib','-static','-fno-builtin','-fno-stack-protector','-march=rv64imafd','-mabi=lp64d',f'-DHART={hart}','-Wl,-Ttext=0x10000','-Wl,--build-id=none',str(path),'-o',str(binary)],check=True)
    source=f'thread-{hart}';sources.append({'id':source,'hartId':hart,'node':f'RN{hart}','title':f'P{hart}','code':path.read_text(),'language':'c'})
    disassembly=subprocess.check_output(['riscv64-linux-gnu-objdump','-d',str(binary)],text=True)
    addresses=[int(m[1],16) for line in disassembly.splitlines() if (m:=re.match(r'\s*([0-9a-f]+):\s+[0-9a-f]+\s+',line))]
    lines=subprocess.check_output(['riscv64-linux-gnu-addr2line','-e',str(binary),*[hex(a) for a in addresses]],text=True).splitlines()
    for address,line in zip(addresses,lines):
        n=re.search(r':(\d+)(?:\s|$)',line)
        if n: pc_map[f'{hart}:{hex(address)}']={'sourceId':source,'line':int(n[1])}
  (out/f'{variant}-sources.json').write_text(json.dumps({'name':{'protocol':'19 类 CPU 协议测试','stress':'四核重试与 dirty victim 压力测试','tutorial':'单 RN 冷 SLC 教学'}[variant],'threads':sources,'pcMap':pc_map,'initial':{'data':'0；测试过程中初始化模式和 dirty owner'},'results':{},'seed':1},ensure_ascii=False))
for variant in ['protocol','stress','tutorial']:build_c(variant)
