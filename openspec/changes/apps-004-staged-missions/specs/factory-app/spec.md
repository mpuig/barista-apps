## ADDED Requirements

### Requirement: A mission SHALL be able to express order between tasks

Factory SHALL accept tasks that declare the tasks they depend on, and SHALL not
start a task before every task it depends on has succeeded. Tasks that do not
depend on one another SHALL still run concurrently, bounded by the mission's
concurrency. A mission whose dependencies cannot be satisfied — a cycle, an
unknown task id, or a task depending on itself — SHALL be refused when the
mission is loaded, before any worker is created.

#### Scenario: a dependent task runs after the task it depends on

- **WHEN** a mission declares that task B depends on task A
- **THEN** B is not started until A has succeeded, and B runs once A has

#### Scenario: independent tasks still run concurrently

- **WHEN** a mission declares tasks that depend on nothing
- **THEN** they run concurrently up to the mission's concurrency, as they did before dependencies existed

#### Scenario: an impossible dependency graph is refused before any work starts

- **WHEN** a mission declares a dependency cycle, a dependency on an unknown task id, or a task that depends on itself
- **THEN** loading the mission fails, naming the offending tasks, and no worker session is created

#### Scenario: a task whose dependency failed is reported as unreachable, not as failed

- **WHEN** a task's dependency does not succeed
- **THEN** the dependent task is reported as blocked, naming the dependency it waited on, and is distinguishable from a task that ran and failed and from one still to be attempted

### Requirement: A dependent task SHALL receive what the task it depends on produced

Factory SHALL let a task declare outputs to capture on success and let a
dependent task declare which of those outputs to receive, placed in the
dependent's own session before its command runs. Outputs SHALL be carried
through the durable coordinator scope, so that a producing worker may be reaped
before its consumer starts.

#### Scenario: a stage hands its output to the next stage

- **WHEN** task A declares an output and task B depends on A and consumes that output
- **THEN** B's session contains that content before B's command runs

#### Scenario: the producer may be reaped before the consumer runs

- **WHEN** task A has succeeded and its worker has been reaped, and task B consumes A's output
- **THEN** B still receives the content

#### Scenario: consuming an output that was never produced is refused before any work starts

- **WHEN** a task consumes an output that no task it depends on produces
- **THEN** loading the mission fails, naming the task and the missing output, and no worker session is created

### Requirement: A task SHALL be judged by a criterion it did not write

Factory SHALL let a mission plant content into a worker's session before the
worker's command runs, and SHALL ensure the planted content is the mission's own
when the task's check runs, whatever the worker did to it in between.

A mission SHALL additionally be able to require that every check in it names
only planted content or content received from a dependency, and such a mission
SHALL be refused at load if any check names anything else. This requirement is
one a mission opts into: a check may name a path as the location to inspect
rather than as the criterion, and those two uses are not distinguishable by
shape.

The isolation a worker runs under bounds what its work can reach; it does not
make the worker's own account of that work into evidence. A criterion fixed by
the mission rather than by the worker is what makes a passing check mean
something.

#### Scenario: the check runs against the mission's criterion, not the worker's

- **WHEN** a mission plants a check's input and the worker overwrites it before the check runs
- **THEN** the check runs against the content the mission planted

#### Scenario: a mission that requires self-authored checks to be impossible is refused when one is present

- **WHEN** a mission requires that checks name only fixed content, and one of its checks names a path that is neither planted nor received from a dependency
- **THEN** loading the mission fails, naming the task and the path, and no worker session is created

#### Scenario: a mission that does not require it is not refused for naming a path

- **WHEN** a mission does not require that checks name only fixed content, and a check names a path as the location it inspects
- **THEN** the mission loads and runs

#### Scenario: planting survives a coordinator restart without duplicating work

- **WHEN** a coordinator restarts after planting content for a task that has not yet succeeded
- **THEN** the task's worker is re-ensured with the same planted content and the task is not run a second time after it has succeeded
