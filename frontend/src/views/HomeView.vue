<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api.js'

const GATE_KEY = 'gate_ambient_c'

const router = useRouter()
const role = ref(localStorage.getItem('role') || '')
const jobs = ref([])
const err = ref('')
// 环境温度为必填：默认空串，未登记/未填写即“缺温”，提交会被拒收。
const form = ref({ lamp: '', nominal_nm: 0.15, measured_nm: 0.15, ambient_c: '' })
let timer

async function refresh() {
  if (!localStorage.getItem('tok')) return
  try {
    jobs.value = await api('/api/jobs')
    err.value = ''
  } catch (e) {
    err.value = String(e.message || e)
  }
}

async function submit() {
  err.value = ''
  const ambient = parseFloat(form.value.ambient_c)
  // 缺温门禁：无温度直接拒收，并写明缺温（后端同样强校验）。
  if (form.value.ambient_c === '' || Number.isNaN(ambient)) {
    err.value = '缺温拒收：环境温度为必填项，请到「温感台」登记或在下方填写环境温度（℃）后再提交'
    return
  }
  try {
    await api('/api/jobs', {
      method: 'POST',
      body: JSON.stringify({
        lamp: form.value.lamp,
        nominal_nm: form.value.nominal_nm,
        measured_nm: form.value.measured_nm,
        ambient_c: ambient,
      }),
    })
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  }
}

function goTemp() {
  router.push('/temperature')
}

function goDetail(id) {
  router.push(`/jobs/${id}`)
}

onMounted(() => {
  role.value = localStorage.getItem('role') || ''
  // 从温感台门禁带入已登记温度（随单冻结）。
  const gate = sessionStorage.getItem(GATE_KEY)
  if (gate !== null) form.value.ambient_c = gate
  refresh()
  timer = setInterval(refresh, 1000)
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div>
    <p v-if="err" style="color:#b00020">{{ err }}</p>
    <section v-if="role === 'writer'" style="margin:16px 0; padding:12px; border:1px solid #ccc;">
      <h3>提交校准</h3>
      <p class="hint">
        环境温度为必填项，可先在
        <a href="#" @click.prevent="goTemp">温感台</a>
        登记；未填温度（缺温）提交将被拒收，温度随单冻结。
      </p>
      <label>灯种 <input v-model="form.lamp" /></label>
      <label>标称 nm <input type="number" step="0.01" v-model.number="form.nominal_nm" /></label>
      <label>实测 nm <input type="number" step="0.01" v-model.number="form.measured_nm" /></label>
      <label class="req">
        环境温度 ℃<span class="star">*</span>
        <input type="number" step="0.1" v-model="form.ambient_c" placeholder="必填，如 24.5" />
      </label>
      <button @click="submit">入队</button>
    </section>
    <table border="1" cellpadding="6" style="border-collapse:collapse; width:100%;">
      <thead>
        <tr>
          <th>编号</th><th>灯种</th><th>标称</th><th>实测</th><th>环境温度(℃)</th><th>状态</th><th>结论</th><th>理由</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="j in jobs"
          :key="j.id"
          style="cursor:pointer"
          @click="goDetail(j.id)"
        >
          <td>{{ j.id }}</td>
          <td>{{ j.lamp }}</td>
          <td>{{ j.nominal_nm }}</td>
          <td>{{ j.measured_nm }}</td>
          <td>{{ j.ambient_c }}</td>
          <td>{{ j.status }}</td>
          <td>{{ j.verdict }}</td>
          <td>{{ j.reason }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.hint {
  color: #666;
  font-size: 13px;
}
.req .star {
  color: #b00020;
  margin-left: 2px;
}
</style>
