import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import '@fontsource-variable/inter'
import '@fontsource-variable/jetbrains-mono'
import '@fortawesome/fontawesome-free/css/all.min.css'
import './style.css'
import App from './App.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: () => import('./views/HomeView.vue') },
    { path: '/d/:domainId', redirect: (to) => `/d/${to.params.domainId}/model` },
    { path: '/d/:domainId/model', component: () => import('./views/ModelView.vue') },
    { path: '/d/:domainId/entity/:entityId', component: () => import('./views/EntityView.vue') },
    { path: '/d/:domainId/invariants', component: () => import('./views/InvariantsView.vue') },
    { path: '/d/:domainId/simulator', component: () => import('./views/SimulatorView.vue') },
    { path: '/d/:domainId/analysis', component: () => import('./views/AnalysisView.vue') },
  ],
})

createApp(App).use(router).mount('#app')
