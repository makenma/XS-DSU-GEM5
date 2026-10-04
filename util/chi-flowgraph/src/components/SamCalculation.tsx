import { useMemo } from 'react';

export default function SamCalculation({sam}:{sam?:Record<string,unknown>}) {
  const calculation=useMemo(()=>{
    if(!sam?.address)return undefined;
    try {
      const address=BigInt(String(sam.address));
      const masks:bigint[]=[];
      let derived=false;
      if(sam.method==='CMN SCG XOR'&&Array.isArray(sam.homeNodeIds)) {
        const count=sam.homeNodeIds.length,paBits=Number(sam.paBits),width=Math.log2(count);
        if(!Number.isInteger(width)||count<1||count>64||!Number.isInteger(paBits)||paBits<6||paBits>64)return undefined;
        for(let output=0;output<width;output++) {
          let mask=0n;for(let bit=6+output;bit<paBits;bit+=width)mask|=1n<<BigInt(bit);
          masks.push(mask);
        }
        derived=true;
      } else if(Array.isArray(sam.masks)) {
        for(const value of sam.masks)masks.push(BigInt(String(value)));
      } else return undefined;
      const rows=masks.map((mask,output)=>{
        let input=address&mask,parity=0;for(let bits=input;bits;bits>>=1n)parity^=Number(bits&1n);
        return {output,mask:`0x${mask.toString(16)}`,input:`0x${input.toString(16)}`,parity};
      });
      return {rows,derived,index:rows.reduce((index,row)=>index+row.parity*2**row.output,0)};
    } catch {return undefined;}
  },[sam]);
  if(!calculation)return null;
  return <div className="sam-calculation">
    <small>{calculation.derived?'按采集的 PA 宽度及 HN 数计算中间结果':'按采集的 XOR mask 计算中间结果'}</small>
    <div className="sam-calculation-row"><span>位</span><span>mask</span><span>PA &amp; mask</span><span>奇偶</span></div>
    {calculation.rows.map(row=><div className="sam-calculation-row" key={row.output}><span>{row.output}</span><code>{row.mask}</code><code>{row.input}</code><b>{row.parity}</b></div>)}
    <p>计算索引 <b>{calculation.index}</b> · 采集索引 {String(sam?.index??'未知')}</p>
  </div>;
}
