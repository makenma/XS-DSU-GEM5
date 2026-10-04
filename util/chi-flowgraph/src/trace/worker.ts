/// <reference lib="webworker" />
import { parseSession, ReplayEngine } from './engine';
import type { WorkerRequest, WorkerReply } from './types';

let engines: [ReplayEngine, ReplayEngine] | undefined;
let loadGeneration=0;
self.onmessage = async ({data}: MessageEvent<WorkerRequest>) => {
  const reply=(message: WorkerReply)=>self.postMessage(message);
  try {
    if (data.command==='load') {
      const generation=++loadGeneration;
      engines=undefined;
      const text=data.file ? await data.file.text() : data.text ?? '';
      if(generation!==loadGeneration)return;
      const session=parseSession(text);
      engines=[new ReplayEngine(session),new ReplayEngine(session,true)];
      reply({requestId:data.requestId,kind:'loaded',session,state:engines[0].seek(-1)});
    } else {
      if (!engines) throw new Error('请先导入轨迹。');
      reply({requestId:data.requestId,kind:'state',state:engines[data.raw?1:0].seek(data.cursor ?? -1)});
    }
  } catch(error) {
    reply({requestId:data.requestId,kind:'error',message:error instanceof Error?error.message:String(error)});
  }
};
