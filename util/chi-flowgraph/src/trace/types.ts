export type Origin = 'observed' | 'derived' | 'modeled' | 'unknown';
export type Channel = 'REQ' | 'RSP' | 'DAT' | 'SNP';
export type Role = 'RN-F' | 'HN-F' | 'SN-F';
export interface TraceNode { id: string; nodeId: number; role: Role; label: string }
export interface FieldValue {
  value: string | number | boolean | null;
  origin: Origin;
  sourceEventId?: string;
  sourceField?: string;
  rule?: string;
  applicable?: boolean;
}
export interface Manifest {
  type: 'manifest'; schemaVersion: '1.0' | '1.1'; title: string; description?: string;
  source: { kind: string; gem5Commit?: string; chironCommit?: string; generator?: string };
  protocol: string; profile: string; dataWidth: number; lineBytes: number;
  topology: TraceNode[]; sam?: { description: string; homeNodeIds?: string[] };
  clock?: { tickHz: string }; initialState?: Record<string, NodeState>;
  complete?: boolean; refinement?: Record<string, unknown>;
  teaching?: {id:string;group:string;topic?:string;evidence:'gem5'|'protocol-model';summary:string;opcode?:string;prerequisites:string[];requirements?:string[];outcome:string;lookup?:{slcHit:boolean;sfHit:boolean};focusRoots:string[];checkpoints:{title:string;eventId:string}[];sources:{label:string;url:string}[]};
  fabric?: { routers: {id:string;x:number;y:number;object?:string}[]; links:{from:string;to:string}[]; attachments:{node:string;router:string}[] };
  litmus?: { name:string; threads:{id:string;hartId:number;node:string;title:string;code:string;language?:string}[]; pcMap?:Record<string,{sourceId:string;line:number}>; initial?:Record<string,unknown>; results?:Record<string,unknown>; seed?:number };
}
export interface Instruction { id:string;hartId:number;iteration:number;dynamicSeq:string;pc:string;sourceId?:string;line?:number;origin?:string;callSite?:{pc:string;sourceId:string;line:number} }
export interface LineState {
  state: string; owner?: string | null; sharers?: string[];
  sfState?: string;
  data?: string; validMask?: string; dirty?: boolean; set?: number; way?: number;
}
export interface NodeState {
  lines: Record<string, LineState>;
  waits: Record<string, { reason: string; rootId: string; since: string; blocker?: string; occupancy?: number; capacity?: number }>;
  inflight: Record<string, string>;
  sam?: Record<string, unknown>;
  resources?: Record<string, { occupancy: number; capacity: number }>;
}
export interface ObservedEvent {
  origin?:Origin;
  type: 'observed'; id: string; seq: number; tick: string; kind: string;
  rootId: string; legId: string; attemptId?: string; parentLegId?: string;
  node?: string; source?: string; target?: string; channel?: Channel; opcode?: string;
  address?: string; fields?: Record<string, unknown>; payload?: string;
  validMask?: string; byteEnable?: string; payloadOrigin?: string;
  state?: LineState; reason?: string; blocker?: string; waitId?: string;
  occupancy?: number; capacity?: number; description?: string;
  messageId?: string; observation?: 'TX' | 'RX';
  causeInstructionIds?:string[]; dataProducerInstructionIds?:string[];
  instructionId?:string;instruction?:Instruction;
}
export interface ProtocolEvent {
  type: 'protocol'; id: string; seq: number; kind: 'flit' | 'binding' | 'complete';
  rootId: string; legId: string; attemptId?: string; parentLegId?: string;
  source: string; target: string; channel: Channel; opcode: string;
  address?: string; fields: Record<string, FieldValue>;
  payload?: string; validMask?: string; byteEnable?: string; payloadOrigin?: string;
  sourceEventIds: string[]; dependencies: string[]; anchorTick: string;
  anchorSeq: number; substep: number; origin: Origin; rule: string; description?: string;
  causeInstructionIds?:string[]; dataProducerInstructionIds?:string[]; messageId?:string;
}
export interface ValidationResult {
  type: 'validation'; id: string; engine: 'Issue H' | 'CHIron E.b' | 'import';
  status: 'pass' | 'fail' | 'insufficient' | 'unsupported';
  rule: string; message: string; eventIds: string[]; rootId?: string;
}
export type TraceRecord = Manifest | ObservedEvent | ProtocolEvent | ValidationResult;
export interface Step {
  id: string; kind: string; rootId: string; address?: string; anchorTick: string;
  event: ProtocolEvent | ObservedEvent; order: number;
}
export interface Session {
  manifest: Manifest; observed: ObservedEvent[]; protocol: ProtocolEvent[];
  validations: ValidationResult[]; steps: Step[]; rawSteps: Step[];
  eventById: Record<string, ProtocolEvent | ObservedEvent>;
  indexes: { refined: Record<string, number>; raw: Record<string, number> };
}
export interface ReplayState { nodes: Record<string, NodeState>; active: Record<string, string>; cursor: number }
export interface WorkerRequest {
  requestId: number; command: 'load' | 'seek'; text?: string; file?: File;
  cursor?: number; raw?: boolean;
}
export type WorkerReply =
  | { requestId: number; kind: 'loaded'; session: Session; state: ReplayState }
  | { requestId: number; kind: 'state'; state: ReplayState }
  | { requestId: number; kind: 'error'; message: string };
