// Register the existing pages and start the local workbench.
import { start } from './router.js';
import { overview } from './pages/overview.js';
import { savedDemo } from './pages/demo.js';
import { skillResults } from './pages/skills.js';
import { profiles } from './pages/profiles.js';
import { questions } from './pages/questions.js';
import { tasks } from './pages/tasks.js';
start({ overview, demo: savedDemo, skills: skillResults, profiles, questions, tasks });
