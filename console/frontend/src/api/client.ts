import axios from 'axios'

export const api = {
  summary: () => axios.get('/api/dashboard/summary').then(r => r.data),
  series: (points = 60) =>
    axios.get('/api/dashboard/series', { params: { points } }).then(r => r.data),
  nodes: () => axios.get('/api/nodes').then(r => r.data),
  addNode: (payload: object) => axios.post('/api/nodes', payload).then(r => r.data),
  installNode: (nodeId: string, payload: object) =>
    axios.post(`/api/nodes/${nodeId}/install`, payload).then(r => r.data),
  removeNode: (nodeId: string) => axios.delete(`/api/nodes/${nodeId}`).then(r => r.data),
  services: () => axios.get('/api/services').then(r => r.data),
  stopService: (node: string, name: string) =>
    axios.post(`/api/services/${node}/${name}/stop`).then(r => r.data),
  startService: (node: string, name: string) =>
    axios.post(`/api/services/${node}/${name}/start`).then(r => r.data),
  serviceLogs: (node: string, name: string, tail = 100) =>
    axios.get(`/api/services/${node}/${name}/logs`, { params: { tail } }).then(r => r.data)
}
