import axios from 'axios'

export const api = {
  summary: () => axios.get('/api/dashboard/summary').then(r => r.data),
  qpsSeries: (points = 48) =>
    axios.get('/api/dashboard/qps-series', { params: { points } }).then(r => r.data),
  nodes: () => axios.get('/api/nodes').then(r => r.data),
  services: () => axios.get('/api/services').then(r => r.data),
  stopService: (node: string, name: string) =>
    axios.post(`/api/services/${node}/${name}/stop`).then(r => r.data)
}
