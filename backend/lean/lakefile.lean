import Lake
open Lake DSL

package screen_tutor_lean where

require mathlib from git
  "https://github.com/leanprover-community/mathlib4" @ "v4.35.0-rc4"

lean_lib ScreenTutor
