import {
  Badge,
  Box,
  Button,
  Drawer,
  DrawerBody,
  DrawerCloseButton,
  DrawerContent,
  DrawerHeader,
  DrawerOverlay,
  Flex,
  Icon,
  Progress,
  Spinner,
  Text,
  Tooltip,
  useColorModeValue,
  useDisclosure,
  VStack,
} from "@chakra-ui/react";
import { MdClose, MdDownload, MdOutlineWorkHistory, MdReplay } from "react-icons/md";
import { useTranslation } from "react-i18next";
import * as api from "../../api/client";
import { useProjectJobs } from "../../hooks/useJob";

const ACTIVE = new Set<api.JobStatus>(["queued", "starting", "running", "cancelling"]);
const RETRYABLE = new Set<api.JobStatus>(["cancelled", "failed", "interrupted"]);

function progressValue(state: api.DurableJobState): number | undefined {
  if (typeof state.current !== "number" || typeof state.total !== "number" || state.total <= 0) {
    return undefined;
  }
  return Math.max(0, Math.min(100, (state.current / state.total) * 100));
}

interface JobsDrawerProps {
  projectId?: string;
  collapsed: boolean;
}

export default function JobsDrawer({ projectId, collapsed }: JobsDrawerProps) {
  const { t } = useTranslation();
  const drawer = useDisclosure();
  const { jobs, activeCount, error, refresh } = useProjectJobs(projectId);
  const textColor = useColorModeValue("navy.700", "white");
  const hoverBg = useColorModeValue(
    "rgba(124, 79, 224, 0.12)",
    "rgba(155, 108, 255, 0.16)",
  );

  if (!projectId) return null;

  const cancel = async (jobId: string) => {
    await api.cancelJob(projectId, jobId);
    await refresh();
  };
  const retry = async (jobId: string) => {
    await api.retryJob(projectId, jobId);
    await refresh();
  };

  return (
    <>
      <Tooltip
        label={t("Background jobs")}
        placement="right"
        hasArrow
        openDelay={350}
        isDisabled={!collapsed}
      >
        <Flex
          as="button"
          type="button"
          onClick={drawer.onOpen}
          aria-label={t("Background jobs")}
          align="center"
          justify={collapsed ? "center" : "flex-start"}
          gap={collapsed ? 0 : 3}
          px={collapsed ? 2 : 4}
          py={2.5}
          borderRadius="lg"
          color={activeCount > 0 ? "purple.300" : textColor}
          bg="transparent"
          _hover={{ bg: hoverBg }}
          transition="all 0.2s"
          w="100%"
        >
          <Box position="relative" display="flex" flexShrink={0}>
            <Icon as={MdOutlineWorkHistory} boxSize={5} />
            {activeCount > 0 && (
              <Badge
                position="absolute"
                top="-9px"
                right="-11px"
                borderRadius="full"
                colorScheme="red"
                fontSize="0.65rem"
                minW="16px"
                textAlign="center"
              >
                {activeCount}
              </Badge>
            )}
          </Box>
          {!collapsed && <Text fontSize="sm">{t("Background jobs")}</Text>}
        </Flex>
      </Tooltip>

      <Drawer isOpen={drawer.isOpen} placement="left" size="md" onClose={drawer.onClose}>
        <DrawerOverlay />
        <DrawerContent bg="app.panel">
          <DrawerCloseButton />
          <DrawerHeader>{t("Background jobs")}</DrawerHeader>
          <DrawerBody>
            {error && <Text color="red.300">{error}</Text>}
            {!error && jobs.length === 0 && <Text color="gray.500">{t("No jobs yet")}</Text>}
            <VStack align="stretch" spacing={3}>
              {jobs.map(({ job, state }) => {
                const progress = progressValue(state);
                return (
                  <Box key={job.job_id} borderWidth="1px" borderRadius="lg" p={3}>
                    <Flex justify="space-between" align="start" gap={3}>
                      <Box minW={0}>
                        <Text fontWeight="semibold">{job.job_type.replace(/_/g, " ")}</Text>
                        <Text fontSize="xs" color="gray.500" noOfLines={1}>{job.job_id}</Text>
                      </Box>
                      <Badge colorScheme={ACTIVE.has(state.status) ? "purple" : state.status === "completed" ? "green" : "gray"}>
                        {state.status}
                      </Badge>
                    </Flex>
                    <Flex mt={2} gap={2} align="center" fontSize="sm" color="gray.400">
                      {ACTIVE.has(state.status) && state.status !== "queued" && <Spinner size="xs" />}
                      <Text>{state.phase || state.status}</Text>
                      <Text>·</Text>
                      <Text>{job.resource_class}</Text>
                    </Flex>
                    {progress !== undefined && <Progress mt={2} size="sm" value={progress} colorScheme="purple" />}
                    {state.error && <Text mt={2} fontSize="xs" color="red.300">{state.error}</Text>}
                    <Flex mt={3} gap={2} justify="flex-end">
                      {state.status === "completed" && Boolean(state.artifacts?.downloadable) && (
                        <Button
                          as="a"
                          size="xs"
                          leftIcon={<MdDownload />}
                          href={api.downloadJobArtifactUrl(projectId, job.job_id)}
                          download
                        >
                          {t("Download")}
                        </Button>
                      )}
                      {ACTIVE.has(state.status) && state.status !== "cancelling" && (
                        <Button size="xs" leftIcon={<MdClose />} onClick={() => void cancel(job.job_id)}>
                          {t("Cancel")}
                        </Button>
                      )}
                      {RETRYABLE.has(state.status) && (
                        <Button size="xs" leftIcon={<MdReplay />} onClick={() => void retry(job.job_id)}>
                          {t("Retry")}
                        </Button>
                      )}
                    </Flex>
                  </Box>
                );
              })}
            </VStack>
          </DrawerBody>
        </DrawerContent>
      </Drawer>
    </>
  );
}
