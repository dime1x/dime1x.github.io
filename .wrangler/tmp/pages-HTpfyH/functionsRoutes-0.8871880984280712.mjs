import { onRequestGet as __instagram_callback_js_onRequestGet } from "C:\\deople\\github\\dime1x.github.io\\functions\\instagram\\callback.js"
import { onRequestGet as __instagram_login_js_onRequestGet } from "C:\\deople\\github\\dime1x.github.io\\functions\\instagram\\login.js"

export const routes = [
    {
      routePath: "/instagram/callback",
      mountPath: "/instagram",
      method: "GET",
      middlewares: [],
      modules: [__instagram_callback_js_onRequestGet],
    },
  {
      routePath: "/instagram/login",
      mountPath: "/instagram",
      method: "GET",
      middlewares: [],
      modules: [__instagram_login_js_onRequestGet],
    },
  ]