/** Build-time choice only. URL parameters or backend failures never enable Demo. */
export const IS_DEMO = process.env.NEXT_PUBLIC_APP_MODE === "demo";
export const DEMO_STORE_KEY = "glasses.demo.data.v1";
export const DEMO_SESSION_KEY = "glasses.demo.session.v1";
