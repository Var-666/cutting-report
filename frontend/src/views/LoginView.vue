<script setup>
import { reactive, ref } from 'vue'
import { bootstrapAccount, loginAccount } from '../api/reports'

const props = defineProps({ setupRequired: Boolean })
const emit = defineEmits(['authenticated'])
const loading = ref(false)
const error = ref('')
const form = reactive({ username: '', display_name: '', password: '' })

async function submit() {
  loading.value = true
  error.value = ''
  try {
    const result = props.setupRequired
      ? await bootstrapAccount(form)
      : await loginAccount({ username: form.username, password: form.password })
    emit('authenticated', result.user)
  } catch (exception) {
    error.value = exception.message
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="login-page">
    <section class="login-card">
      <div class="login-brand"><span></span><span></span><span></span></div>
      <p class="eyebrow">WORKSHOP PRODUCTION</p>
      <h1>{{ setupRequired ? '初始化管理员账号' : '登录生产系统' }}</h1>
      <p>{{ setupRequired ? '首次使用需要创建管理员，随后由管理员建立工人账号。' : '登录后系统会自动使用当前账号记录任务确认人。' }}</p>
      <form @submit.prevent="submit">
        <label><span>登录账号</span><input v-model.trim="form.username" minlength="2" maxlength="64" autocomplete="username" required /></label>
        <label v-if="setupRequired"><span>显示姓名</span><input v-model.trim="form.display_name" maxlength="64" required /></label>
        <label><span>密码</span><input v-model="form.password" type="password" minlength="8" maxlength="128" autocomplete="current-password" required /></label>
        <div v-if="error" class="login-error">{{ error }}</div>
        <button class="primary" type="submit" :disabled="loading">{{ loading ? '正在提交' : setupRequired ? '创建并登录' : '登录' }}</button>
      </form>
    </section>
  </main>
</template>
