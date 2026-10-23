export type Provider = 'vlm' | 'llm'
export interface ModelConfig {base_url:string;model:string;mode:'local'|'cloud';api_key_set?:boolean;api_key?:string;clear_api_key?:boolean}
export interface AppConfig {models:Record<Provider,ModelConfig>;cad:{backend:string;transport:string;command:string;args:string[];endpoint:string;timeout_seconds:number;converter_path?:string;converter_kind?:string;version?:string;auth_token_env?:string;remote_output_root?:string;template_path?:string};agents:{enabled_roles:string[];assignments:Record<string,Provider>;prompt_overrides:Record<string,string>;max_parallel:number;max_calls:number;max_seconds:number;estimated_cost_per_call:number;max_estimated_cost:number};pipeline:{dpi:number;tile_size:number;overlap:number;confidence_threshold:number;keep_markups:boolean};[key:string]:unknown}
export interface Agent {id:string;name:string;group:string;description:string;default_provider:Provider|'deterministic';required:boolean;enabled?:boolean}
export interface DrawingFile {id:string;name:string;size:number;pages:number;kind:string;page_info?:unknown[];inspection_error?:string;created_at?:string}
export interface JobEvent {seq:number;time:string;agent?:string;status:string;message:string;progress?:number}
export interface Artifact {id:string;name:string;url:string;kind:string}
export interface Job {id:string;type:string;status:string;progress:number;message?:string;file_ids?:string[];options?:Record<string,unknown>;events?:JobEvent[];artifacts?:Artifact[];result?:Record<string,unknown>;error?:string;created_at?:string}
export interface GraphElement {id:string;kind:string;subclass:string;tag:string|null;text:string;bbox:number[];points:number[][];rotation:number;parent_equipment:string|null;connected_to:string[];confidence:number;tile_id:string;source_agent:string;status:string;attributes:Record<string,string>}
export interface Graph {schema_version:string;page_id:string;width_px:number;height_px:number;width_mm:number;height_mm:number;elements:GraphElement[];edges:{id:string;from_id:string;to_id:string;kind:string}[];review:{id:string;element_id?:string;reason:string;status:string;evidence?:string}[]}
export interface Health {status:string;version:string;mode:string;auth_required?:boolean}
export const activeJob = (job?:Job|null) => !!job && ['queued','running','cancelling'].includes(job.status)
export class ApiError extends Error {status:number;constructor(message:string,status:number){super(message);this.status=status}}
function headers(json=false):Record<string,string> {const token=sessionStorage.getItem('lineaforge-token');return {...(json?{'Content-Type':'application/json'}:{}),...(token?{'Authorization':`Bearer ${token}`}:{})}}
export async function request<T>(path:string,options:RequestInit={}):Promise<T> {let response:Response;try{response=await fetch(path,{...options,headers:{...headers(!!options.body && !(options.body instanceof FormData)),...options.headers}})}catch{throw new ApiError('The backend is unreachable. Start LineaForge on this machine or check the server address.',0)}if(!response.ok){let detail=`Request failed (${response.status})`;try{const body=await response.json();detail=typeof body.detail==='string'?body.detail:body.message||JSON.stringify(body.detail||body)}catch{/* non-JSON server response */}throw new ApiError(detail,response.status)}if(response.status===204)return undefined as T;return response.json()}
export const api = {
 health:()=>request<Health>('/api/health'),config:()=>request<AppConfig>('/api/config'),saveConfig:(config:AppConfig)=>request<AppConfig>('/api/config',{method:'PUT',body:JSON.stringify(config)}),
 agents:()=>request<{agents:Agent[]}>('/api/agents'),files:()=>request<{files:DrawingFile[]}>('/api/files'),jobs:()=>request<{jobs:Job[]}>('/api/jobs'),job:(id:string)=>request<Job>(`/api/jobs/${encodeURIComponent(id)}`),
 upload:async(file:File)=>{const form=new FormData();form.append('file',file);return request<DrawingFile>('/api/files',{method:'POST',body:form})},
 createJob:(type:string,file_ids:string[],options:Record<string,unknown>,cloud_consent=false)=>request<Job>('/api/jobs',{method:'POST',body:JSON.stringify({type,file_ids,options,cloud_consent})}),
 cancel:(id:string)=>request<Job>(`/api/jobs/${id}/cancel`,{method:'POST'}),retry:(id:string)=>request<Job>(`/api/jobs/${id}/retry`,{method:'POST'}),
 graph:(id:string)=>request<Graph>(`/api/jobs/${id}/graph`),saveGraph:(id:string,graph:Graph)=>request<Graph>(`/api/jobs/${id}/graph`,{method:'PUT',body:JSON.stringify(graph)}),
 review:(id:string,body:Record<string,unknown>)=>request<Graph>(`/api/jobs/${id}/review`,{method:'POST',body:JSON.stringify(body)}),
 testModel:(kind:Provider)=>request<{ok:boolean;message:string;models?:string[]}>('/api/models/test',{method:'POST',body:JSON.stringify({kind})}),
 testCad:(cloud_consent=false)=>request<{ok:boolean;message:string;tools?:unknown[]}>('/api/cad/test',{method:'POST',body:JSON.stringify({cloud_consent})}),
 preview:async(id:string,page=0)=>{const response=await fetch(`/api/files/${id}/preview?page=${page}`,{headers:headers()});if(!response.ok)throw new Error('Preview is unavailable for this file.');return URL.createObjectURL(await response.blob())},
 artifactImage:async(artifact:Artifact)=>{const response=await fetch(artifact.url,{headers:headers()});if(!response.ok)throw new Error('Evidence image unavailable.');return URL.createObjectURL(await response.blob())},
 download:async(artifact:Artifact)=>{const response=await fetch(artifact.url,{headers:headers()});if(!response.ok)throw new Error(`Download failed (${response.status})`);const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download=artifact.name;a.click();setTimeout(()=>URL.revokeObjectURL(url),30000)},
}
