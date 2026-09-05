import {Config} from "@remotion/cli/config";

// Only Python may read the private TTS .env. Remotion exposes dotenv values to scenes.
Config.setDotEnvLocation(".env.render");
