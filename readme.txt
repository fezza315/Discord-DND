any script in DM stuff can only be run my the designated DM or through the bots terminal.
this is a discord bot and will use user ID's to identify so if people change their usernames they dont lose their char.

upon /first-start the bot will have the reqired atrebutes:
DM: @<[user ID]>
each channel ID
and the bot will create from these atr's:
create a DM roll that can acsess the DM section(will ask for ID in cmd)
create a "current turn" and "not current turn" roll and for everyone restrict chatting running commands in play(ask for ID in cmd)
create a "player" roll and then send a "join the session" message in apply to play(ID in cmd) with a button that makes the bot direct message the user with a command template to enter your base controllable stats (such as class, race etc) and when the command is run ask for confermation as this cannot be changed.
    after the user creates a char:
        roll stats and then show the final char sheet and inv.
        change the users in server nickname to their char's entered name.
        give everyone the "not current turn" roll and then roll a dice in player and order from highest to lowest for turn order(DM and their cretures always first)

DM command table:
/start
/create
/spawn
/edit
and these can be paired with - phrases:
-campain(cannot be used on /spawn)
-monstor(cannot be used on /start)
-map(cannot be used on /start)
-player(can only be used on /edit or when dead /spawn(will make them classed as a monstor and is encoraged to attack the other camapin members))

fill in other cmds with whats needed.

any references to dirs that start with / instead of the dir is places in the main.py root, exsample is "/tables/effects.dt"
this is in the main.py root as writen but if "/player data/how to.txt" calls "exsample player/inv.dt" that is in ref to "how to.txt"

to depict the current player turn the main script will first run "/player data/player turn.py(check)" and it should return the the char ID and the user ID that controls it, unless it is the DM then it just returns the char ID and then plain "DM"

players can create a char with /create-char that will send a request to the DM with a direct message writing
"@<[user ID]> wants to create a char"
with two buttons "allow, disallow" when allow is clicked the bot will show a char creation menu to the player with the race, class and name adn then makes them roll for each stat in real time and then shows them the final stats before saving them to the char and then presenting the char sheet to the DM.

the server will have channels in this format

Dm
    overview
    char-sheets
    maping(a map can be drawn out with scrawl and then you can uplaoad the map from dungeon scawl with the old school module and a locked 1x1 grid with a green square for the start and a algorithem figures out what are rooms, what are walkways/hallways and what are doors and uploads them into "/tables/rooms.dt" by default set to unmarked except the spawn room, an optinal description can be set, otherwise as a player walks in the turn is halted, the DM gets a ping and the bot awaits a description(entered with "/room-dsc 'text') once the DM writes one the turn resumes and the room gets its description uploaded to the rooms.dt and it moves on
    event-planning(have it so when a room is entered it auto starts a full table event)

players
    play
    map-view(only shows the discovered map between all the players, each room gets marked in "/tables/rooms.dt" under the marked atribute)


for any dm action after the cmd is run it wont take effect until they /apply
where it will show what will happen after the cmd and then /confirm actually makes it take effect


------------------------------------------------------------------
IMPLEMENTATION NOTES (added with the finished bot - your text above is unchanged)
- channels now also include:  Dm / dm-logs (players + gameplay log)   and a Dev category: logs (every message, edits,
  deletions) + server-messages (all DM stuff, DM actions, misc).  See SETUP.txt section E.
- DM commands are staged: /apply previews, /confirm applies, /discard cancels.
- "/start -campain" etc. are the slash subcommands:  /start campaign, /create monster, /spawn map, /edit player ...
- characters live in "player data/player <id>/" and are tied to Discord users through "player data/bindings.dt".
